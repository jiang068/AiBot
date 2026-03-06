import base64
import html
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Union
import ssl
import os
import aiohttp
from PIL import Image
import io
from src.common.logger import get_module_logger
from nonebot import get_driver

from ..models.utils_model import LLM_request
from .config import global_config
from .mapper import emojimapper
from .message_base import Seg
from .utils_user import get_user_nickname, get_groupname
from .message_base import GroupInfo, UserInfo

driver = get_driver()
config = driver.config

# 创建SSL上下文
ssl_context = ssl.create_default_context()
ssl_context.set_ciphers("AES128-GCM-SHA256")

logger = get_module_logger("cq_code")


@dataclass
class CQCode:
    """
    CQ码数据类，用于存储和处理CQ码

    属性:
        type: CQ码类型（如'image', 'at', 'face'等）
        params: CQ码的参数字典
        raw_code: 原始CQ码字符串
        translated_segments: 经过处理后的Seg对象列表
    """

    type: str
    params: Dict[str, str]
    group_info: Optional[GroupInfo] = None
    user_info: Optional[UserInfo] = None
    translated_segments: Optional[Union[Seg, List[Seg]]] = None
    reply_message: Dict = None  # 存储回复消息
    image_base64: Optional[str] = None
    _llm: Optional[LLM_request] = None

    def __post_init__(self):
        """初始化LLM实例"""
        pass

    async def translate(self):
        """根据CQ码类型进行相应的翻译处理，转换为Seg对象"""
        if self.type == "text":
            self.translated_segments = Seg(type="text", data=self.params.get("text", ""))
        elif self.type == "image":
            base64_data = await self.translate_image()
            if base64_data:
                if self.params.get("sub_type") == "0":
                    # 普通图片：携带 base64 供引用时按需描述
                    self.translated_segments = Seg(type="image", data=base64_data)
                else:
                    # 表情包：异步保存文件到 data/emoji/（供 scan_new_emojis 注册），Seg 携带 base64 供实时描述
                    self._save_emoji_async(base64_data)
                    self.translated_segments = Seg(type="emoji", data=base64_data)
            else:
                self.translated_segments = Seg(type="text", data="[图片]")
        elif self.type == "at":
            if self.params.get("qq") == "all":
                self.translated_segments = Seg(type="text", data="@[全体成员]")
            else:
                qq = self.params.get("qq", "")
                # 如果 @ 的是 bot 自己，插入固定标记方便后续检测
                if str(qq) == str(global_config.BOT_QQ):
                    self.translated_segments = Seg(type="text", data=f"[@{global_config.BOT_NICKNAME}]")
                else:
                    user_nickname = get_user_nickname(qq)
                    self.translated_segments = Seg(type="text", data=f"[@{user_nickname or '某人'}]")
        elif self.type == "reply":
            reply_segments = await self.translate_reply()
            if reply_segments:
                self.translated_segments = Seg(type="seglist", data=reply_segments)
            else:
                self.translated_segments = Seg(type="text", data="[回复某人消息]")
        elif self.type == "face":
            face_id = self.params.get("id", "")
            self.translated_segments = Seg(type="text", data=f"[{emojimapper.get(int(face_id), '表情')}]")
        elif self.type == "forward":
            forward_segments = await self.translate_forward()
            if forward_segments:
                self.translated_segments = Seg(type="seglist", data=forward_segments)
            else:
                self.translated_segments = Seg(type="text", data="[转发消息]")
        else:
            self.translated_segments = Seg(type="text", data=f"[{self.type}]")

    async def get_img(self) -> Optional[str]:
        """异步获取图片并转换为base64"""
        url = html.unescape(self.params["url"])
        if not url.startswith(("http://", "https://")):
            return None

        # 准备两套 headers：
        # - 第一套用旧 UA（兼容一些简单服务器）
        # - 第二套用现代 Chrome UA + Referer（专门应对腾讯多媒体链接）
        headers_list = [
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/50.0.2661.87 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cache-Control": "no-cache",
            },
            {
                # 腾讯多媒体专用：现代 UA + Referer，修复 400 问题
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://qq.com/",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        ]

        max_retries = 3
        for retry in range(max_retries):
            # 遇到腾讯域名时优先使用现代 headers；其他域名先用旧 headers
            is_tencent = "multimedia.nt.qq.com.cn" in url or "gchat.qpic.cn" in url
            headers = headers_list[1] if is_tencent else headers_list[retry % len(headers_list)]
            try:
                logger.debug(f"获取图片中 (retry={retry}): {url[:80]}")
                conn = aiohttp.TCPConnector(ssl=ssl_context)
                async with aiohttp.ClientSession(connector=conn) as session:
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=20),
                        allow_redirects=True,
                    ) as response:
                        if response.status == 400 and is_tencent:
                            # 腾讯多媒体 400：链接可能已过期或需要不同头部，换头部再重试
                            logger.warning(f"[图片下载] 腾讯图片返回400，切换headers重试 (retry={retry})")
                            # 此次失败，换另一套 headers 继续下一次循环
                            headers = headers_list[retry % len(headers_list)]
                            await asyncio.sleep(1)
                            continue

                        if response.status != 200:
                            raise aiohttp.ClientError(f"HTTP {response.status}")

                        # 验证内容类型
                        content_type = response.headers.get("Content-Type", "")
                        if not content_type.startswith("image/"):
                            raise ValueError(f"非图片内容类型: {content_type}")

                        content = await response.read()
                        logger.debug(f"获取图片成功: {url[:80]}")
                        image_base64 = base64.b64encode(content).decode("utf-8")
                        self.image_base64 = image_base64
                        return image_base64

            except (aiohttp.ClientError, ValueError) as e:
                if retry == max_retries - 1:
                    logger.error(f"[图片下载] 最终请求失败: {str(e)}")
                await asyncio.sleep(1.5 ** retry)

            except Exception as e:
                logger.exception(f"[图片下载] 发生未知错误: {str(e)}")
                return None

        return None

    async def translate_image(self) -> Optional[str]:
        """处理图片类型的CQ码，返回base64字符串"""
        if "url" not in self.params:
            return None
        return await self.get_img()

    def _save_emoji_async(self, base64_data: str) -> None:
        """将表情包 base64 异步写入 data/emoji/ 目录，供 scan_new_emojis 定期扫描注册。
        写文件操作在后台 task 中执行，不阻塞消息处理流程。"""
        import hashlib as _hashlib
        image_bytes = base64.b64decode(base64_data)
        image_hash = _hashlib.md5(image_bytes).hexdigest()

        if global_config.EMOJI_SAVE:
            async def _write():
                try:
                    emoji_dir = os.path.join("data", "emoji")
                    os.makedirs(emoji_dir, exist_ok=True)
                    image_format = Image.open(io.BytesIO(image_bytes)).format or "jpeg"
                    file_path = os.path.join(emoji_dir, f"{image_hash}.{image_format.lower()}")
                    if not os.path.exists(file_path):
                        with open(file_path, "wb") as f:
                            f.write(image_bytes)
                        logger.debug(f"[抓取] 保存表情包: {file_path}")
                except Exception:
                    logger.exception("[抓取] 保存表情包文件失败")

            asyncio.create_task(_write())

    async def translate_forward(self) -> Optional[List[Seg]]:
        """处理转发消息，返回Seg列表"""
        try:
            if "content" not in self.params:
                return None

            content = self.unescape(self.params["content"])
            import ast

            try:
                messages = ast.literal_eval(content)
            except ValueError as e:
                logger.error(f"解析转发消息内容失败: {str(e)}")
                return None

            formatted_segments = []
            for msg in messages:
                sender = msg.get("sender", {})
                nickname = sender.get("card") or sender.get("nickname", "未知用户")
                raw_message = msg.get("raw_message", "")
                message_array = msg.get("message", [])

                if message_array and isinstance(message_array, list):
                    for message_part in message_array:
                        if message_part.get("type") == "forward":
                            content_seg = Seg(type="text", data="[转发消息]")
                            break
                        else:
                            if raw_message:
                                from .message_cq import MessageRecvCQ

                                user_info = UserInfo(
                                    platform="qq",
                                    user_id=msg.get("user_id", 0),
                                    user_nickname=nickname,
                                )
                                group_info = GroupInfo(
                                    platform="qq",
                                    group_id=msg.get("group_id", 0),
                                    group_name=get_groupname(msg.get("group_id", 0)),
                                )

                                message_obj = MessageRecvCQ(
                                    message_id=msg.get("message_id", 0),
                                    user_info=user_info,
                                    raw_message=raw_message,
                                    plain_text=raw_message,
                                    group_info=group_info,
                                )
                                await message_obj.initialize()
                                content_seg = Seg(type="seglist", data=[message_obj.message_segment])
                            else:
                                content_seg = Seg(type="text", data="[空消息]")
                else:
                    if raw_message:
                        from .message_cq import MessageRecvCQ

                        user_info = UserInfo(
                            platform="qq",
                            user_id=msg.get("user_id", 0),
                            user_nickname=nickname,
                        )
                        group_info = GroupInfo(
                            platform="qq",
                            group_id=msg.get("group_id", 0),
                            group_name=get_groupname(msg.get("group_id", 0)),
                        )
                        message_obj = MessageRecvCQ(
                            message_id=msg.get("message_id", 0),
                            user_info=user_info,
                            raw_message=raw_message,
                            plain_text=raw_message,
                            group_info=group_info,
                        )
                        await message_obj.initialize()
                        content_seg = Seg(type="seglist", data=[message_obj.message_segment])
                    else:
                        content_seg = Seg(type="text", data="[空消息]")

                formatted_segments.append(Seg(type="text", data=f"{nickname}: "))
                formatted_segments.append(content_seg)
                formatted_segments.append(Seg(type="text", data="\n"))

            return formatted_segments

        except Exception as e:
            logger.error(f"处理转发消息失败: {str(e)}")
            return None

    async def translate_reply(self) -> Optional[List[Seg]]:
        """处理回复类型的CQ码，返回Seg列表"""
        from .message_cq import MessageRecvCQ
        from .utils_image import ImageManager

        if self.reply_message is None:
            return None
        if hasattr(self.reply_message, "group_id"):
            group_info = GroupInfo(platform="qq", group_id=self.reply_message.group_id, group_name="")
        else:
            group_info = None

        if self.reply_message.sender.user_id:
            message_obj = MessageRecvCQ(
                user_info=UserInfo(
                    user_id=self.reply_message.sender.user_id, user_nickname=self.reply_message.sender.nickname
                ),
                message_id=self.reply_message.message_id,
                raw_message=str(self.reply_message.message),
                group_info=group_info,
            )
            await message_obj.initialize()

            segments = []
            if message_obj.message_info.user_info.user_id == global_config.BOT_QQ:
                segments.append(Seg(type="text", data=f"[回复 {global_config.BOT_NICKNAME} 的消息: "))
            else:
                segments.append(
                    Seg(
                        type="text",
                        data=f"[回复 {self.reply_message.sender.nickname} 的消息: ",
                    )
                )

            # 对引用消息中的 image/emoji 段按需调 VLM 描述，其他段正常传递
            replied_seg = message_obj.message_segment
            described_seg = await self._describe_reply_segment(replied_seg, ImageManager())
            segments.append(described_seg)
            segments.append(Seg(type="text", data="]"))
            return segments
        else:
            return None

    async def _describe_reply_segment(self, seg: Seg, image_manager) -> Seg:
        """递归遍历 Seg 树，将 image/emoji 段替换为描述文本"""
        if seg.type == "seglist":
            new_children = []
            for child in seg.data:
                new_children.append(await self._describe_reply_segment(child, image_manager))
            return Seg(type="seglist", data=new_children)
        elif seg.type == "image" and isinstance(seg.data, str):
            # 普通图片：base64 按需 VLM 描述
            description = await image_manager.describe_for_reply(seg.data, is_emoji=False)
            return Seg(type="text", data=description)
        elif seg.type == "emoji" and isinstance(seg.data, str):
            # 先用 md5 查 emoji DB，有描述直接用，避免重复调 VLM
            import hashlib as _hashlib
            image_bytes = base64.b64decode(seg.data)
            image_hash = _hashlib.md5(image_bytes).hexdigest()
            from ...common.database import db as _db
            emoji_record = _db.emoji.find_one({"hash": image_hash})
            if emoji_record and emoji_record.get("discription"):
                desc = emoji_record["discription"]
                logger.info(f"[引用表情包] 命中库描述: {desc}")
                return Seg(type="text", data=f"[表情包：{desc}]")
            # 库里还没有（尚未被 scan 处理）→ 按需 VLM
            description = await image_manager.describe_for_reply(seg.data, is_emoji=True)
            return Seg(type="text", data=description)
        else:
            return seg

    @staticmethod
    def unescape(text: str) -> str:
        """反转义CQ码中的特殊字符"""
        return text.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


class CQCode_tool:
    @staticmethod
    def cq_from_dict_to_class(cq_code: Dict, msg, reply: Optional[Dict] = None) -> CQCode:
        """
        将CQ码字典转换为CQCode对象

        Args:
            cq_code: CQ码字典
            msg: MessageCQ对象
            reply: 回复消息的字典（可选）

        Returns:
            CQCode对象
        """
        # 处理字典形式的CQ码
        # 从cq_code字典中获取type字段的值,如果不存在则默认为'text'
        cq_type = cq_code.get("type", "text")
        params = {}
        if cq_type == "text":
            params["text"] = cq_code.get("data", {}).get("text", "")
        else:
            params = cq_code.get("data", {})

        instance = CQCode(
            type=cq_type,
            params=params,
            group_info=msg.message_info.group_info,
            user_info=msg.message_info.user_info,
            reply_message=reply,
        )

        return instance

    @staticmethod
    def create_reply_cq(message_id: int) -> str:
        """
        创建回复CQ码
        Args:
            message_id: 回复的消息ID
        Returns:
            回复CQ码字符串
        """
        return f"[CQ:reply,id={message_id}]"

    @staticmethod
    def create_emoji_cq(file_path: str) -> str:
        """
        创建表情包CQ码
        Args:
            file_path: 本地表情包文件路径
        Returns:
            表情包CQ码字符串
        """
        # 确保使用绝对路径
        abs_path = os.path.abspath(file_path)
        # 转义特殊字符
        escaped_path = abs_path.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=file:///{escaped_path},sub_type=1]"

    @staticmethod
    def create_emoji_cq_base64(base64_data: str) -> str:
        """
        创建表情包CQ码
        Args:
            base64_data: base64编码的表情包数据
        Returns:
            表情包CQ码字符串
        """
        # 转义base64数据
        escaped_base64 = (
            base64_data.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        )
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=base64://{escaped_base64},sub_type=1]"

    @staticmethod
    def create_image_cq_base64(base64_data: str) -> str:
        """
        创建表情包CQ码
        Args:
            base64_data: base64编码的表情包数据
        Returns:
            表情包CQ码字符串
        """
        # 转义base64数据
        escaped_base64 = (
            base64_data.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        )
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=base64://{escaped_base64},sub_type=0]"


cq_code_tool = CQCode_tool()
