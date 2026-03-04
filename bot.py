import asyncio
import os
import sys
import platform
import time
from pathlib import Path

import nonebot
import uvicorn
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter
from src.common.logger import get_module_logger

logger = get_module_logger("main_bot")
env_mask = {key: os.getenv(key) for key in os.environ}

uvicorn_server = None
driver = None
app = None
loop = None

def load_env():
    if os.path.exists("config/.env"):
        load_dotenv("config/.env", override=True)
        logger.success("成功加载基础环境变量配置")
    else:
        logger.error(".env 文件不存在")
        raise FileNotFoundError(".env 文件不存在")
    env = os.getenv("ENVIRONMENT")
    logger.info(f"[load_env] 当前的 ENVIRONMENT 变量值：{env}")
    
def scan_provider(env_config: dict):
    # 这些是 WebUI / 框架自用的内置字段，不是第三方提供商密钥，跳过扫描
    BUILTIN_KEYS = {"SECRET_KEY", "WEBUI_USERNAME", "WEBUI_PASSWORD"}
    provider = {}
    env_config = dict(filter(lambda item: item[0] not in env_mask, env_config.items()))
    for key in env_config:
        if key in BUILTIN_KEYS:
            continue
        if key.endswith("_BASE_URL") or key.endswith("_KEY"):
            provider_name = key.split("_", 1)[0]
            if provider_name not in provider:
                provider[provider_name] = {"url": None, "key": None}
            if key.endswith("_BASE_URL"):
                provider[provider_name]["url"] = env_config[key]
            elif key.endswith("_KEY"):
                provider[provider_name]["key"] = env_config[key]
    for provider_name, config in provider.items():
        if config["url"] is None or config["key"] is None:
            raise ValueError(f"请检查 '{provider_name}' 提供商配置是否丢失 BASE_URL 或 KEY 环境变量")

async def graceful_shutdown():
    try:
        global uvicorn_server
        if uvicorn_server:
            uvicorn_server.force_exit = True
            await uvicorn_server.shutdown()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Aibot关闭失败: {e}")

async def uvicorn_main():
    global uvicorn_server
    config = uvicorn.Config(
        app="__main__:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", 8080)),
        reload=os.getenv("ENVIRONMENT") == "dev",
        timeout_graceful_shutdown=5,
        log_config=None,
        access_log=True,
    )
    server = uvicorn.Server(config)
    uvicorn_server = server
    await server.serve()

def raw_main():
    if platform.system().lower() != "windows":
        time.tzset()
    load_env()
    env_config = {key: os.getenv(key) for key in os.environ}
    scan_provider(env_config)

    base_config = {
        "websocket_port": int(env_config.get("PORT", 8080)),
        "host": env_config.get("HOST", "127.0.0.1"),
        "log_level": "INFO",
    }
    nonebot.init(**base_config, **env_config)
    global driver
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    try:
        raw_main()
        app = nonebot.get_asgi()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(uvicorn_main())
        except KeyboardInterrupt:
            logger.warning("收到中断信号，正在优雅关闭...")
            loop.run_until_complete(graceful_shutdown())
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"主程序异常: {str(e)}")
        if loop and not loop.is_closed():
            loop.run_until_complete(graceful_shutdown())
            loop.close()
        sys.exit(1)