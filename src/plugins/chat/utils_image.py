import base64
import re
from typing import Optional
from PIL import Image
import io

from nonebot import get_driver

from ..chat.config import global_config
from ..models.utils_model import LLM_request

from src.common.logger import get_module_logger

logger = get_module_logger("chat_image")

driver = get_driver()
config = driver.config


class ImageManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._initialized = True
            if global_config.vlm and global_config.vlm.get("name", "").strip():
                self._llm = LLM_request(model=global_config.vlm, temperature=0.4, max_tokens=1000, request_type="image")
            else:
                self._llm = None

    @staticmethod
    def _filter_description(description: str, is_emoji: bool = False) -> Optional[str]:
        """过滤异常描述，返回 None 表示不可用。

        对于表情包（is_emoji=True）不要简单地基于字数丢弃：
        - 不再因为长度稍长就直接丢弃，长度过长时改为截断后保留。
        - 对中文字符的严格度放宽，避免把正常的表情描述误判为无效。
        仍保留重复内容和包含 HTML 标签等明显异常的过滤。
        """
        if not description:
            return None
        description = description.strip("[]").replace("表情包：", "").replace("图片：", "").strip()
        if not description:
            return None

        # 对表情包放宽长度限制：不直接丢弃，超过一定长度则截断；非表情包仍以 60 字为上限丢弃
        if not is_emoji:
            if len(description) > 60:
                logger.warning(f"[过滤] 描述过长（{len(description)}字），已丢弃: {description[:40]}...")
                return None
        else:
            # 表情包允许更长的描述，但超过 200 字会被截断为 200 字
            if len(description) > 200:
                logger.info(f"[过滤] 表情包描述过长（{len(description)}字），已截断为200字: {description[:40]}...")
                description = description[:200]

        # 2. 前8字出现≥3次 → 重复幻觉（对表情包仍适用）
        if len(description) >= 8 and description.count(description[:8]) >= 3:
            logger.warning(f"[过滤] 描述存在大量重复，已丢弃: {description[:40]}...")
            return None

        # 3. 中文字符检查：对非表情包要求较严格，对表情包放宽
        chinese_chars = re.sub(r'[^\u4e00-\u9fff]', '', description)
        if not is_emoji and len(chinese_chars) < 4:
            logger.warning(f"[过滤] 描述无实质中文内容，已丢弃: {description[:40]}")
            return None

        # 4. 含 HTML 标签
        if re.search(r'<[a-zA-Z]+[\s>/]', description):
            logger.warning(f"[过滤] 描述包含HTML标签，已丢弃: {description[:40]}")
            return None

        return description

    async def describe_for_reply(self, image_base64: str, is_emoji: bool = False) -> str:
        """仅在引用消息时按需调用，直接返回描述文本，不缓存不存文件"""
        if not self._llm:
            return "[表情包]" if is_emoji else "[图片]"
        try:
            image_bytes = base64.b64decode(image_base64)
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()
            if is_emoji:
                prompt = (
                    "这是一张表情包图片。请用一句中文简洁描述：图中是什么角色或事物、表情或动作是什么、"
                    "传达什么情感。要求：只输出描述本身，不超过30个字，不要编号、不要换行、不要重复内容。"
                )
            else:
                prompt = (
                    "请用中文描述这张图片的内容。如果有文字，请把文字都描述出来。"
                    "并尝试猜测这个图片的含义。最多100个字。"
                )
            raw, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)
            description = self._filter_description(raw, is_emoji=is_emoji)
            if description:
                logger.info(f"[引用图片描述] {description}")
                return f"[表情包：{description}]" if is_emoji else f"[图片：{description}]"
            else:
                return "[表情包]" if is_emoji else "[图片]"
        except Exception as e:
            logger.error(f"引用图片描述失败: {str(e)}")
            return "[表情包]" if is_emoji else "[图片]"

    # ---- 以下方法保留供 emoji_manager.scan_new_emojis 使用 ----

    async def get_emoji_description(self, image_base64: str) -> str:
        """供 emoji_manager 扫描注册时使用，返回 '[表情包：xxx]' 或 '[表情包]'"""
        return await self.describe_for_reply(image_base64, is_emoji=True)






def image_path_to_base64(image_path: str) -> str:
    """将图片路径转换为base64编码
    Args:
        image_path: 图片文件路径
    Returns:
        str: base64编码的图片数据
    """
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
            return base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        logger.error(f"读取图片失败: {image_path}, 错误: {str(e)}")
        return None
