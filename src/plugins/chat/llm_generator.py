import random
import time
from typing import List, Optional, Tuple, Union

from nonebot import get_driver

from ...common.database import db
from ..models.utils_model import LLM_request
from .config import global_config
from .message import MessageRecv, MessageThinking, Message
from .prompt_builder import prompt_builder
from .utils import process_llm_response
from src.common.logger import get_module_logger, LogConfig, LLM_STYLE_CONFIG

# 定义日志配置
llm_config = LogConfig(
    # 使用消息发送专用样式
    console_format=LLM_STYLE_CONFIG["console_format"],
    file_format=LLM_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("llm_generator", config=llm_config)

driver = get_driver()
config = driver.config


class ResponseGenerator:
    def __init__(self):
        self.model_r1 = LLM_request(
            model=global_config.llm_reasoning,
            temperature=0.7,
            max_tokens=1000,
            stream=True,
        )
        self.model_v3 = LLM_request(model=global_config.llm_normal, temperature=0.7, max_tokens=3000)
        self.model_r1_distill = LLM_request(model=global_config.llm_reasoning_minor, temperature=0.7, max_tokens=3000)
        self.model_v25 = LLM_request(model=global_config.llm_normal_minor, temperature=0.7, max_tokens=3000)
        # 专门用于情感判断的模型
        self.model_emotion = LLM_request(model=global_config.llm_emotion_judge, temperature=0.7, max_tokens=500)
        self.current_model_type = "r1"  # 默认使用 R1

    async def generate_response(self, message: MessageThinking) -> Optional[Union[str, List[str]]]:
        """智能响应生成 - 支持单次/多次API调用模式切换"""
        # 检查是否启用单次API调用模式
        if global_config.SINGLE_API_MODE:
            return await self._generate_unified_response_mode(message)
        else:
            return await self._generate_legacy_response_mode(message)

    async def _generate_unified_response_mode(self, message: MessageThinking) -> Optional[Union[str, List[str]]]:
        """单次API调用模式"""
        # 从global_config中获取模型概率值并选择模型
        rand = random.random()
        if rand < global_config.MODEL_R1_PROBABILITY:
            self.current_model_type = "r1"
            current_model = self.model_r1
        elif rand < global_config.MODEL_R1_PROBABILITY + global_config.MODEL_V3_PROBABILITY:
            self.current_model_type = "v3"
            current_model = self.model_v3
        else:
            self.current_model_type = "r1_distill"
            current_model = self.model_r1_distill

        logger.info(f"{global_config.BOT_NICKNAME}{self.current_model_type}思考中 [单次API模式]")

        # 使用统一的单次API调用
        model_response = await self._generate_unified_response(message, current_model)
        
        if model_response:
            # 解析统一响应
            response_text, stance, emotion = self._parse_unified_response(model_response)

            if response_text:
                logger.info(f"{global_config.BOT_NICKNAME}的回复是：{response_text}")
                processed_response = await self._process_response(response_text)

                # 存储情感分析结果供后续使用
                self._cached_emotion_result = (stance, emotion)

                if processed_response:
                    return processed_response, response_text

        return None, model_response

    async def _generate_legacy_response_mode(self, message: MessageThinking) -> Optional[Union[str, List[str]]]:
        """传统多次API调用模式"""
        # 从global_config中获取模型概率值并选择模型
        rand = random.random()
        if rand < global_config.MODEL_R1_PROBABILITY:
            self.current_model_type = "r1"
            current_model = self.model_r1
        elif rand < global_config.MODEL_R1_PROBABILITY + global_config.MODEL_V3_PROBABILITY:
            self.current_model_type = "v3"
            current_model = self.model_v3
        else:
            self.current_model_type = "r1_distill"
            current_model = self.model_r1_distill

        logger.info(f"{global_config.BOT_NICKNAME}{self.current_model_type}思考中 [多次API模式]")

        model_response = await self._generate_response_with_model(message, current_model)
        raw_content = model_response

        if model_response:
            logger.info(f"{global_config.BOT_NICKNAME}的回复是：{model_response}")
            model_response = await self._process_response(model_response)
            if model_response:
                return model_response, raw_content
        return None, raw_content

    async def _generate_unified_response(self, message: MessageThinking, model: LLM_request) -> Optional[str]:
        """统一的单次API调用，包含所有功能"""
        sender_name = ""
        if message.chat_stream.user_info.user_cardname and message.chat_stream.user_info.user_nickname:
            sender_name = (
                f"[({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}]"
                f"{message.chat_stream.user_info.user_cardname}"
            )
        elif message.chat_stream.user_info.user_nickname:
            sender_name = f"({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}"
        else:
            sender_name = f"用户({message.chat_stream.user_info.user_id})"

        # 构建统一prompt，包含所有功能
        unified_prompt = await self._build_unified_prompt(
            message.chat_stream,
            message_txt=message.processed_plain_text,
            sender_name=sender_name,
            stream_id=message.chat_stream.stream_id,
        )
        
        try:
            # 进行统一的API调用
            response, _ = await model.generate_response(unified_prompt)
            return response
        except Exception as e:
            logger.error(f"统一API调用失败: {e}")
            return None

    async def _generate_response_with_model(self, message: MessageThinking, model: LLM_request) -> Optional[str]:
        """使用指定的模型生成回复"""
        sender_name = ""
        if message.chat_stream.user_info.user_cardname and message.chat_stream.user_info.user_nickname:
            sender_name = (
                f"[({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}]"
                f"{message.chat_stream.user_info.user_cardname}"
            )
        elif message.chat_stream.user_info.user_nickname:
            sender_name = f"({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}"
        else:
            sender_name = f"用户({message.chat_stream.user_info.user_id})"

        # 构建prompt
        prompt, prompt_check = await prompt_builder._build_prompt(
            message.chat_stream,
            message_txt=message.processed_plain_text,
            sender_name=sender_name,
            stream_id=message.chat_stream.stream_id,
        )

        # 生成回复
        try:
            content, reasoning_content = await model.generate_response(prompt)
        except Exception:
            logger.exception("生成回复时出错")
            return None

        # 保存到数据库
        self._save_to_db(
            message=message,
            sender_name=sender_name,
            prompt=prompt,
            prompt_check=prompt_check,
            content=content,
            reasoning_content=reasoning_content,
        )

        return content

    def _save_to_db(
        self,
        message: MessageRecv,
        sender_name: str,
        prompt: str,
        prompt_check: str,
        content: str,
        reasoning_content: str,
    ):
        """保存对话记录到数据库"""
        db.reasoning_logs.insert_one(
            {
                "time": time.time(),
                "chat_id": message.chat_stream.stream_id,
                "user": sender_name,
                "message": message.processed_plain_text,
                "model": self.current_model_type,
                # 'reasoning_check': reasoning_content_check,
                # 'response_check': content_check,
                "reasoning": reasoning_content,
                "response": content,
                "prompt": prompt,
                "prompt_check": prompt_check,
            }
        )

    async def _get_emotion_tags(self, content: str, processed_plain_text: str):
        """提取情感标签，结合立场和情绪"""
        try:
            # 构建提示词，结合回复内容、被回复的内容以及立场分析
            prompt = f"""
            请根据以下对话内容，完成以下任务：
            1. 判断回复者的立场是"supportive"（支持）、"opposed"（反对）还是"neutrality"（中立）。
            2. 从"happy,angry,sad,surprised,disgusted,fearful,neutral"中选出最匹配的1个情感标签。
            3. 按照"立场-情绪"的格式输出结果，例如："supportive-happy"。

            被回复的内容：
            {processed_plain_text}

            回复内容：
            {content}

            请分析回复者的立场和情感倾向，并输出结果：
            """

            # 调用模型生成结果
            result, _ = await self.model_emotion.generate_response(prompt)
            result = result.strip()

            # 解析模型输出的结果
            if "-" in result:
                stance, emotion = result.split("-", 1)
                valid_stances = ["supportive", "opposed", "neutrality"]
                valid_emotions = ["happy", "angry", "sad", "surprised", "disgusted", "fearful", "neutral"]
                if stance in valid_stances and emotion in valid_emotions:
                    return stance, emotion  # 返回有效的立场-情绪组合
                else:
                    return "neutrality", "neutral"  # 默认返回中立-中性
            else:
                return "neutrality", "neutral"  # 格式错误时返回默认值

        except Exception as e:
            print(f"获取情感标签时出错: {e}")
            return "neutrality", "neutral"  # 出错时返回默认值

    async def _build_unified_prompt(self, chat_stream, message_txt: str, sender_name: str, stream_id: str) -> str:
        """构建统一的prompt，包含所有功能"""
        # 重用原有的prompt构建逻辑，但不调用API
        prompt, _ = await prompt_builder._build_prompt(
            chat_stream, message_txt, sender_name, stream_id, unified_mode=True
        )
        
        # 在原有prompt基础上添加统一任务指令
        unified_instruction = """

请按以下格式完成任务：

<TOPICS>识别消息主题（2-4个词，用逗号分隔，如果没有明显主题写"无主题"）</TOPICS>
<RESPONSE>根据上述信息生成回复</RESPONSE>
<EMOTION>分析你的回复立场和情感，格式为"立场-情感"，立场选择：supportive/opposed/neutrality，情感选择：happy/angry/sad/surprised/disgusted/fearful/neutral</EMOTION>

用户消息：""" + message_txt

        return prompt + unified_instruction

    def _parse_unified_response(self, response: str) -> tuple:
        """解析统一API响应"""
        try:
            # 提取主题
            topics = "无主题"
            if "<TOPICS>" in response and "</TOPICS>" in response:
                topics_match = response.split("<TOPICS>")[1].split("</TOPICS>")[0].strip()
                if topics_match:
                    topics = topics_match
            
            # 提取回复内容
            response_text = ""
            if "<RESPONSE>" in response and "</RESPONSE>" in response:
                response_match = response.split("<RESPONSE>")[1].split("</RESPONSE>")[0].strip()
                if response_match:
                    response_text = response_match
            elif "<RESPONSE>" in response:
                # 如果只有开始标签，取到EMOTION标签或文件结尾
                response_part = response.split("<RESPONSE>")[1]
                if "<EMOTION>" in response_part:
                    response_text = response_part.split("<EMOTION>")[0].strip()
                else:
                    response_text = response_part.strip()
            
            # 提取情感分析
            stance, emotion = "neutrality", "neutral"
            if "<EMOTION>" in response and "</EMOTION>" in response:
                emotion_match = response.split("<EMOTION>")[1].split("</EMOTION>")[0].strip()
                if "-" in emotion_match:
                    parts = emotion_match.split("-", 1)
                    stance, emotion = parts[0].strip(), parts[1].strip()
            
            # 记录解析的主题信息
            if topics != "无主题":
                topic_list = [t.strip() for t in topics.split(",") if t.strip()]
                logger.info(f"识别主题: {topic_list}")
            
            return response_text, stance, emotion
            
        except Exception as e:
            logger.error(f"解析统一响应失败: {e}")
            # 如果解析失败，尝试直接返回原始响应作为回复内容
            return response.strip(), "neutrality", "neutral"

    def get_cached_emotion_result(self):
        """获取缓存的情感分析结果"""
        return getattr(self, '_cached_emotion_result', ("neutrality", "neutral"))

    async def _process_response(self, content: str) -> Tuple[List[str], List[str]]:
        """处理响应内容，返回处理后的内容"""
        if not content:
            return None

        return process_llm_response(content)
