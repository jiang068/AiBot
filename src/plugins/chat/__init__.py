import asyncio
import os
import shutil
import time
from pathlib import Path

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, NoticeEvent
from nonebot.typing import T_State

from ..moods.moods import MoodManager  # 导入情绪管理器
from ..schedule.schedule_generator import bot_schedule
from ..utils.statistic import LLMStatistics
from .bot import chat_bot
from .config import global_config
from .emoji_manager import emoji_manager
from .relationship_manager import relationship_manager
from ..willing.willing_manager import willing_manager
from .chat_stream import ChatManager
from ..memory_system.memory import hippocampus
from .message_sender import message_manager, message_sender
from .storage import MessageStorage
from src.common.logger import get_module_logger

logger = get_module_logger("chat_init")

# 创建LLM统计实例
llm_stats = LLMStatistics("data/llm_statistics.txt")

# 添加标志变量
_message_manager_started = False
_memory_task_lock = asyncio.Lock()


def _has_disk_headroom() -> bool:
    """磁盘空间不足时跳过记忆构建，避免继续放大写盘压力。"""
    db_path = Path(os.getenv("SQLITE_DB_PATH", "data/aibot.db")).expanduser()
    probe_path = db_path.parent if db_path.parent.exists() else Path.cwd()
    usage = shutil.disk_usage(probe_path)
    minimum_gb = float(os.getenv("AIBOT_MIN_FREE_SPACE_GB", "1"))
    minimum_bytes = int(minimum_gb * 1024 ** 3)
    if usage.free < minimum_bytes:
        logger.error(
            f"[资源保护] 数据库所在磁盘仅剩 {usage.free / 1024 ** 3:.2f} GB，"
            f"低于保护阈值 {minimum_gb:.2f} GB，跳过记忆构建"
        )
        return False
    return True

# 获取驱动器
driver = get_driver()
config = driver.config

# 初始化表情管理器
emoji_manager.initialize()

logger.debug(f"正在唤醒{global_config.BOT_NICKNAME}......")
# 注册消息处理器
msg_in = on_message(priority=5)
# 注册和bot相关的通知处理器
notice_matcher = on_notice(priority=1)
# 创建定时任务
scheduler = require("nonebot_plugin_apscheduler").scheduler


@driver.on_startup
async def start_background_tasks():
    """启动后台任务"""
    # 启动LLM统计
    llm_stats.start()
    logger.success("LLM统计功能启动成功")

    # 初始化并启动情绪管理器
    mood_manager = MoodManager.get_instance()
    mood_manager.start_mood_update(update_interval=global_config.mood_update_interval)
    logger.success("情绪管理器启动成功")

    # 只启动表情包管理任务
    asyncio.create_task(emoji_manager.start_periodic_check(interval_MINS=global_config.EMOJI_CHECK_INTERVAL))
    try:
        await bot_schedule.initialize()
        bot_schedule.print_schedule()
    except Exception:
        logger.exception("[日程] 初始化失败，已跳过；机器人其余功能继续启动")


@driver.on_startup
async def init_relationships():
    """在 NoneBot2 启动时初始化关系管理器"""
    logger.debug("正在加载用户关系数据...")
    await relationship_manager.load_all_relationships()
    asyncio.create_task(relationship_manager._start_relationship_manager())


@driver.on_bot_connect
async def _(bot: Bot):
    """Bot连接成功时的处理"""
    global _message_manager_started
    logger.debug(f"-----------{global_config.BOT_NICKNAME}成功连接！-----------")
    await willing_manager.ensure_started()

    message_sender.set_bot(bot)
    logger.success("-----------消息发送器已启动！-----------")

    if not _message_manager_started:
        asyncio.create_task(message_manager.start_processor())
        _message_manager_started = True
        logger.success("-----------消息处理器已启动！-----------")

    if global_config.EMOJI_SAVE:
        asyncio.create_task(emoji_manager._periodic_scan(interval_MINS=global_config.EMOJI_REGISTER_INTERVAL))
        logger.success("-----------开始偷表情包！-----------")
    else:
        logger.info("-----------表情包抓取已禁用（EMOJI_ENABLED=false）-----------")
    asyncio.create_task(ChatManager()._initialize())
    asyncio.create_task(ChatManager()._auto_save_task())


@msg_in.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    #处理合并转发消息
    if "forward" in event.message:
        await chat_bot.handle_forward_message(event , bot)
    else :
        await chat_bot.handle_message(event, bot)

@notice_matcher.handle()
async def _(bot: Bot, event: NoticeEvent, state: T_State):
    logger.debug(f"收到通知：{event}")
    await chat_bot.handle_notice(event, bot)


# 添加build_memory定时任务
@scheduler.scheduled_job(
    "interval",
    seconds=global_config.build_memory_interval,
    id="build_memory",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=30,
)
async def build_memory_task():
    """每build_memory_interval秒执行一次记忆构建"""
    if _memory_task_lock.locked():
        logger.warning("[记忆构建] 上一次记忆任务仍在运行，跳过本次调度")
        return
    if not _has_disk_headroom():
        return

    async with _memory_task_lock:
        await _build_memory_task()


async def _build_memory_task():
    logger.debug("[记忆构建]------------------------------------开始构建记忆--------------------------------------")
    start_time = time.time()
    # 限制单次批量，避免消息高峰时记忆整理占满事件循环和磁盘。
    await hippocampus.operation_build_memory(chat_size=12)
    end_time = time.time()
    logger.success(
        f"[记忆构建]--------------------------记忆构建完成：耗时: {end_time - start_time:.2f} "
        "秒-------------------------------------------"
    )


@scheduler.scheduled_job(
    "interval",
    seconds=global_config.forget_memory_interval,
    id="forget_memory",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=30,
)
async def forget_memory_task():
    """周期性执行低成本遗忘检查，避免和记忆构建重叠。"""
    if _memory_task_lock.locked():
        logger.warning("[记忆遗忘] 记忆构建或其他遗忘任务仍在运行，跳过本次调度")
        return

    async with _memory_task_lock:
        await _forget_memory_task()


async def _forget_memory_task():
    print("\033[1;32m[记忆遗忘]\033[0m 开始遗忘记忆...")
    await hippocampus.operation_forget_topic(percentage=global_config.memory_forget_percentage)
    print("\033[1;32m[记忆遗忘]\033[0m 记忆遗忘完成")


@scheduler.scheduled_job("interval", seconds=global_config.build_memory_interval + 10, id="merge_memory")
async def merge_memory_task():
    """每30秒执行一次记忆构建"""
    # print("\033[1;32m[记忆整合]\033[0m 开始整合")
    # await hippocampus.operation_merge_memory(percentage=0.1)
    # print("\033[1;32m[记忆整合]\033[0m 记忆整合完成")


@scheduler.scheduled_job("interval", seconds=30, id="print_mood")
async def print_mood_task():
    """每30秒打印一次情绪状态"""
    mood_manager = MoodManager.get_instance()
    mood_manager.print_mood_status()


@scheduler.scheduled_job("interval", seconds=7200, id="generate_schedule")
async def generate_schedule_task():
    """每2小时尝试生成一次日程"""
    logger.debug("尝试生成日程")
    try:
        await bot_schedule.initialize()
        if not bot_schedule.enable_output:
            bot_schedule.print_schedule()
    except Exception:
        logger.exception("[日程] 定时更新失败，本次跳过")


@scheduler.scheduled_job("interval", seconds=3600, id="remove_recalled_message")
async def remove_recalled_message() -> None:
    """删除撤回消息"""
    try:
        storage = MessageStorage()
        await storage.remove_recalled_message(time.time())
    except Exception:
        logger.exception("删除撤回消息失败")
