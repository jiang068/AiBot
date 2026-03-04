import asyncio
import base64
import hashlib
import os
import random
import time
import traceback
from typing import Optional, Tuple
from PIL import Image
import io

from nonebot import get_driver

from ...common.database import db
from ..chat.config import global_config
from ..chat.utils import get_embedding
from ..chat.utils_image import ImageManager, image_path_to_base64
from ..models.utils_model import LLM_request
from src.common.logger import get_module_logger

logger = get_module_logger("emoji")


driver = get_driver()
config = driver.config


class EmojiManager:
    _instance = None
    EMOJI_DIR = os.path.join("data", "emoji")  # 表情包存储目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        self._scan_task = None
        self.vlm = LLM_request(model=global_config.vlm, temperature=0.3, max_tokens=1000, request_type="image")
        self.llm_emotion_judge = LLM_request(
            model=global_config.llm_emotion_judge, max_tokens=600, temperature=0.8, request_type="image"
        )  # 更高的温度，更少的token（后续可以根据情绪来调整温度）

    def _ensure_emoji_dir(self):
        """确保表情存储目录存在"""
        os.makedirs(self.EMOJI_DIR, exist_ok=True)

    def initialize(self):
        """初始化数据库连接和表情目录"""
        if not self._initialized:
            try:
                self._ensure_emoji_collection()
                self._ensure_emoji_dir()
                self._initialized = True
                # 启动时执行一次完整性检查
                self.check_emoji_file_integrity()
            except Exception:
                logger.exception("初始化表情管理器失败")

    def _ensure_db(self):
        """确保数据库已初始化"""
        if not self._initialized:
            self.initialize()
        if not self._initialized:
            raise RuntimeError("EmojiManager not initialized")

    def _ensure_emoji_collection(self):
        """确保emoji表已存在（SQLite 建表在 database.py 的 __init_tables 中完成，这里无需操作）"""
        pass

    def record_usage(self, emoji_id: str):
        """记录表情使用次数"""
        try:
            self._ensure_db()
            db.emoji.update_one({"id": emoji_id}, {"$inc": {"usage_count": 1}})
        except Exception as e:
            logger.error(f"记录表情使用失败: {str(e)}")

    async def get_emoji_for_text(self, text: str) -> Optional[Tuple[str, str]]:
        """根据文本内容获取相关表情包
        Args:
            text: 输入文本
        Returns:
            Optional[str]: 表情包文件路径，如果没有找到则返回None


        可不可以通过 配置文件中的指令 来自定义使用表情包的逻辑？
        我觉得可行

        """
        try:
            self._ensure_db()

            if not global_config.EMOJI_SEND_ENABLED:
                return None

            # 获取文本的embedding
            text_for_search = await self._get_kimoji_for_text(text)
            if not text_for_search:
                logger.error("无法获取文本的情绪")
                return None
            text_embedding = await get_embedding(text_for_search)
            if not text_embedding:
                logger.error("无法获取文本的embedding")
                return None

            try:
                # 获取所有表情包
                all_emojis = list(db.emoji.find({}))

                if not all_emojis:
                    logger.warning("数据库中没有任何表情包")
                    return None

                # 计算余弦相似度并排序
                def cosine_similarity(v1, v2):
                    if not v1 or not v2:
                        return 0
                    dot_product = sum(a * b for a, b in zip(v1, v2))
                    norm_v1 = sum(a * a for a in v1) ** 0.5
                    norm_v2 = sum(b * b for b in v2) ** 0.5
                    if norm_v1 == 0 or norm_v2 == 0:
                        return 0
                    return dot_product / (norm_v1 * norm_v2)

                # 计算所有表情包与输入文本的相似度
                emoji_similarities = [
                    (emoji, cosine_similarity(text_embedding, emoji.get("embedding", []))) for emoji in all_emojis
                ]

                # 按相似度降序排序
                emoji_similarities.sort(key=lambda x: x[1], reverse=True)

                # 获取前3个最相似的表情包
                top_10_emojis = emoji_similarities[: 10 if len(emoji_similarities) > 10 else len(emoji_similarities)]

                if not top_10_emojis:
                    logger.warning("未找到匹配的表情包")
                    return None

                # 从前3个中随机选择一个
                selected_emoji, similarity = random.choice(top_10_emojis)

                if selected_emoji and "path" in selected_emoji:
                    # 更新使用次数
                    db.emoji.update_one({"id": selected_emoji["id"]}, {"$inc": {"usage_count": 1}})

                    logger.info(
                        f"[匹配] 找到表情包: {selected_emoji.get('discription', '无描述')} (相似度: {similarity:.4f})"
                    )
                    # 稍微改一下文本描述，不然容易产生幻觉，描述已经包含 表情包 了
                    return selected_emoji["path"], "[ %s ]" % selected_emoji.get("discription", "无描述")

            except Exception as search_error:
                logger.error(f"[错误] 搜索表情包失败: {str(search_error)}")
                return None

            return None

        except Exception as e:
            logger.error(f"[错误] 获取表情包失败: {str(e)}")
            return None

    async def _get_emoji_discription(self, image_base64: str) -> str:
        """获取表情包的描述标签，复用 ImageManager 的描述生成与过滤逻辑"""
        try:
            result = await ImageManager().get_emoji_description(image_base64)
            # get_emoji_description 返回 "[表情包：xxx]" 或 "[表情包]"
            if result == "[表情包]":
                return None
            # 剥掉格式包装，只返回描述文本
            description = result.strip("[]").replace("表情包：", "").strip()
            return description if description else None
        except Exception as e:
            logger.error(f"[错误] 获取表情包描述失败: {str(e)}")
            return None

    async def _check_emoji(self, image_base64: str, image_format: str) -> str:
        try:
            prompt = (
                f'这是一个表情包，请回答这个表情包是否满足"{global_config.EMOJI_CHECK_PROMPT}"的要求，是则回答是，'
                f"否则回答否，不要出现任何其他内容"
            )

            content, _ = await self.vlm.generate_response_for_image(prompt, image_base64, image_format)
            logger.debug(f"[检查] 表情包检查结果: {content}")
            return content

        except Exception as e:
            logger.error(f"[错误] 表情包检查失败: {str(e)}")
            return None

    async def _get_kimoji_for_text(self, text: str):
        try:
            prompt = (
                f"这是{global_config.BOT_NICKNAME}将要发送的消息内容:\n{text}\n若要为其配上表情包，"
                f"请你输出这个表情包应该表达怎样的情感，应该给人什么样的感觉，不要太简洁也不要太长，"
                f'注意不要输出任何对消息内容的分析内容，只输出"一种什么样的感觉"中间的形容词部分。'
            )

            content, _ = await self.llm_emotion_judge.generate_response_async(prompt, temperature=1.5)
            logger.info(f"[情感] 表情包情感描述: {content}")
            return content

        except Exception as e:
            logger.error(f"[错误] 获取表情包情感失败: {str(e)}")
            return None

    async def scan_new_emojis(self):
        """扫描新的表情包"""
        try:
            emoji_dir = self.EMOJI_DIR
            os.makedirs(emoji_dir, exist_ok=True)

            # 获取所有支持的图片文件
            files_to_process = [
                f for f in os.listdir(emoji_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
            ]

            for filename in files_to_process:
                image_path = os.path.join(emoji_dir, filename)

                # 获取图片的base64编码和哈希值
                image_base64 = image_path_to_base64(image_path)
                if image_base64 is None:
                    os.remove(image_path)
                    continue

                image_bytes = base64.b64decode(image_base64)
                image_hash = hashlib.md5(image_bytes).hexdigest()
                image_format = Image.open(io.BytesIO(image_bytes)).format.lower()
                # 检查是否已经注册过
                existing_emoji = db.emoji.find_one({"hash": image_hash})
                description = None

                if existing_emoji:
                    # 即使表情包已存在，也检查是否需要同步到images集合
                    description = existing_emoji.get("discription")
                    # 检查是否在images集合中存在
                    existing_image = db.images.find_one({"hash": image_hash})
                    if not existing_image:
                        # 同步到images集合
                        image_doc = {
                            "hash": image_hash,
                            "path": image_path,
                            "type": "emoji",
                            "description": description,
                            "timestamp": int(time.time()),
                        }
                        db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                        logger.success(f"[同步] 已同步表情包到images集合: {filename}")
                    continue

                # 检查是否在images集合中已有描述
                existing_image_doc = db.images.find_one({"hash": image_hash, "type": "emoji"})
                existing_description = existing_image_doc.get("description") if existing_image_doc else None

                if existing_description:
                    description = existing_description
                else:
                    # 检查是否配置了VLM模型
                    if global_config.vlm and global_config.vlm.get("name", "").strip():
                        # 获取表情包的描述
                        description = await self._get_emoji_discription(image_base64)
                    else:
                        # 没有VLM模型时，使用默认描述
                        description = f"表情包_{filename}"
                        logger.info(f"[跳过AI描述] 没有VLM模型，使用默认描述: {description}")

                if global_config.EMOJI_CHECK and description and description != f"表情包_{filename}":
                    # 只有在有真实描述时才进行AI检查
                    check = await self._check_emoji(image_base64, image_format)
                    if "是" not in check:
                        os.remove(image_path)
                        logger.info(f"[过滤] 表情包描述: {description}")
                        logger.info(f"[过滤] 表情包不满足规则，已移除: {check}")
                        continue
                    logger.info(f"[检查] 表情包检查通过: {check}")

                # 直接保存表情包，不再检查 description 是否为 None
                embedding = await get_embedding(description) if description else []
                # 准备数据库记录
                emoji_record = {
                    "filename": filename,
                    "path": image_path,
                    "embedding": embedding,
                    "discription": description or f"表情包_{filename}",
                    "hash": image_hash,
                    "timestamp": int(time.time()),
                }

                # 滚动上限：超过 EMOJI_MAX_COUNT 时删除最旧的一条
                if global_config.EMOJI_MAX_COUNT > 0:
                    current_count = db.emoji.count_documents({})
                    if current_count >= global_config.EMOJI_MAX_COUNT:
                        all_emojis = sorted(db.emoji.find(), key=lambda x: x.get("timestamp", 0))
                        oldest = all_emojis[0] if all_emojis else None
                        if oldest:
                            old_path = oldest.get("path", "")
                            if old_path and os.path.exists(old_path):
                                try:
                                    os.remove(old_path)
                                except Exception:
                                    pass
                            db.emoji.delete_one({"id": oldest["id"]})
                            logger.info(
                                f"[滚动] 表情包库已达上限({global_config.EMOJI_MAX_COUNT})，"
                                f"已删除最旧: {oldest.get('filename', '')}"
                            )

                # 保存到emoji数据库
                db.emoji.insert_one(emoji_record)
                logger.success(f"[注册] 新表情包: {filename}")
                logger.info(f"[描述] {description or f'表情包_{filename}'}")

                # 保存到images数据库
                image_doc = {
                    "hash": image_hash,
                    "path": image_path,
                    "type": "emoji",
                    "description": description or f"表情包_{filename}",
                    "timestamp": int(time.time()),
                }
                db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                logger.success(f"[同步] 已保存到images集合: {filename}")

        except Exception:
            logger.exception("[错误] 扫描表情包失败")

    async def _periodic_scan(self, interval_MINS: int = 10):
        """定期扫描新表情包"""
        while True:
            logger.info("[扫描] 开始扫描新表情包...")
            await self.scan_new_emojis()
            await asyncio.sleep(interval_MINS * 60)  # 每600秒扫描一次

    def check_emoji_file_integrity(self):
        """检查表情包文件完整性
        如果文件已被删除，则从数据库中移除对应记录
        """
        try:
            self._ensure_db()
            # 获取所有表情包记录
            all_emojis = list(db.emoji.find())
            removed_count = 0
            total_count = len(all_emojis)

            for emoji in all_emojis:
                try:
                    if "path" not in emoji or not emoji["path"]:
                        logger.warning(f"[检查] 发现无效记录（缺少path字段），ID: {emoji.get('id', 'unknown')}")
                        db.emoji.delete_one({"id": emoji["id"]})
                        removed_count += 1
                        continue

                    if "embedding" not in emoji:
                        logger.warning(f"[检查] 发现过时记录（缺少embedding字段），ID: {emoji.get('id', 'unknown')}")
                        db.emoji.delete_one({"id": emoji["id"]})
                        removed_count += 1
                        continue

                    # 检查文件是否存在
                    if not os.path.exists(emoji["path"]):
                        logger.warning(f"[检查] 表情包文件已被删除: {emoji['path']}")
                        # 从数据库中删除记录
                        result = db.emoji.delete_one({"id": emoji["id"]})
                        if result.deleted_count > 0:
                            logger.debug(f"[清理] 成功删除数据库记录: {emoji['id']}")
                            removed_count += 1
                        else:
                            logger.error(f"[错误] 删除数据库记录失败: {emoji['id']}")
                        continue

                    if "hash" not in emoji or not emoji["hash"]:
                        logger.warning(f"[检查] 发现缺失记录（缺少hash字段），ID: {emoji.get('id', 'unknown')}")
                        new_hash = hashlib.md5(open(emoji["path"], "rb").read()).hexdigest()
                        db.emoji.update_one({"id": emoji["id"]}, {"$set": {"hash": new_hash}})

                except Exception as item_error:
                    logger.error(f"[错误] 处理表情包记录时出错: {str(item_error)}")
                    continue

            # 验证清理结果
            remaining_count = db.emoji.count_documents({})
            if removed_count > 0:
                logger.success(f"[清理] 已清理 {removed_count} 个失效的表情包记录")
                logger.info(f"[统计] 清理前: {total_count} | 清理后: {remaining_count}")
            else:
                logger.info(f"[检查] 已检查 {total_count} 个表情包记录")

        except Exception as e:
            logger.error(f"[错误] 检查表情包完整性失败: {str(e)}")
            logger.error(traceback.format_exc())

    async def start_periodic_check(self, interval_MINS: int = 120):
        while True:
            self.check_emoji_file_integrity()
            await asyncio.sleep(interval_MINS * 60)


# 创建全局单例

emoji_manager = EmojiManager()
