"""
Aibot WebUI
作为主入口启动，负责管理 bot.py 进程
"""
import asyncio
import os
import re
import sys
import signal
import platform
import subprocess
from pathlib import Path
from typing import Dict, Any, List
import toml
import configparser
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pydantic import BaseModel

from fastapi import FastAPI, Request, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv(dotenv_path=Path("config") / ".env")

CONFIG_DIR = Path("config")
ENV_FILE = CONFIG_DIR / ".env"
TOML_FILE = CONFIG_DIR / "bot_config.toml"
BOT_SCRIPT = "bot.py"

# 用于去除终端 ANSI 颜色代码的正则，保证前端日志干净
ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# --- 安全与认证配置 ---
def _ensure_credentials():
    """确保 SECRET_KEY、WEBUI_USERNAME 和 WEBUI_PASSWORD 已设置，如果未设置则自动生成"""
    import secrets as _secrets
    import string as _string
    import re as _re

    updated = False

    # 检查并生成 SECRET_KEY
    secret_key = os.getenv("SECRET_KEY", "").strip()
    placeholder = "a_very_secret_key_change_me_32_chars"
    if not secret_key or secret_key == placeholder:
        secret_key = _secrets.token_hex(32)
        os.environ["SECRET_KEY"] = secret_key
        updated = True
        print(f"[WebUI] SECRET_KEY 未设置，已自动生成")

    # 检查并生成 WEBUI_USERNAME
    username = os.getenv("WEBUI_USERNAME", "").strip()
    if not username or username == "admin":
        chars = _string.ascii_letters + _string.digits
        username = ''.join(_secrets.choice(chars) for _ in range(12))
        os.environ["WEBUI_USERNAME"] = username
        updated = True
        print(f"[WebUI] WEBUI_USERNAME 未设置或为默认值，已自动生成: {username}")

    # 检查并生成 WEBUI_PASSWORD
    password = os.getenv("WEBUI_PASSWORD", "").strip()
    if not password or password == "admin":
        chars = _string.ascii_letters + _string.digits
        password = ''.join(_secrets.choice(chars) for _ in range(12))
        os.environ["WEBUI_PASSWORD"] = password
        updated = True
        print(f"[WebUI] WEBUI_PASSWORD 未设置或为默认值，已自动生成: {password}")

    # 如果有任何更新，写回 .env 文件
    if updated and ENV_FILE.exists():
        raw = ENV_FILE.read_text(encoding="utf-8")

        if _re.search(r'^SECRET_KEY\s*=', raw, flags=_re.MULTILINE):
            raw = _re.sub(r'^(SECRET_KEY\s*=).*$', f'SECRET_KEY={secret_key}', raw, flags=_re.MULTILINE)
        else:
            raw += f'\nSECRET_KEY={secret_key}\n'

        if _re.search(r'^WEBUI_USERNAME\s*=', raw, flags=_re.MULTILINE):
            raw = _re.sub(r'^(WEBUI_USERNAME\s*=).*$', f'WEBUI_USERNAME={username}', raw, flags=_re.MULTILINE)
        else:
            raw += f'\nWEBUI_USERNAME={username}\n'

        if _re.search(r'^WEBUI_PASSWORD\s*=', raw, flags=_re.MULTILINE):
            raw = _re.sub(r'^(WEBUI_PASSWORD\s*=).*$', f'WEBUI_PASSWORD={password}', raw, flags=_re.MULTILINE)
        else:
            raw += f'\nWEBUI_PASSWORD={password}\n'

        ENV_FILE.write_text(raw, encoding="utf-8")
        print(f"[WebUI] 凭证已保存到 {ENV_FILE}")

    return secret_key, username, password

SECRET_KEY, WEBUI_USERNAME, WEBUI_PASSWORD = _ensure_credentials()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login/token")

class TokenData(BaseModel):
    username: str | None = None

# --- 认证辅助函数 ---
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    if token_data.username != WEBUI_USERNAME:
        raise credentials_exception
    return token_data.username

async def get_current_user_from_token(token: str):
    """从令牌字符串中验证并获取用户，专用于 WebSocket"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username != WEBUI_USERNAME:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception

# --- 配置文件解析函数 ---
def parse_env_file(content: str) -> List[Dict[str, str]]:
    """解析 .env 文件内容为结构化数据"""
    items = []
    lines = content.split('\n')
    
    for line in lines:
        line = line.strip()
        
        # 保留空行（用于保持文件格式）
        if not line:
            items.append({
                "key": "",
                "value": "",
                "comment": "",
                "type": "blank"
            })
            continue
            
        # 处理纯注释行
        if line.startswith('#'):
            items.append({
                "key": "",
                "value": "",
                "comment": line[1:].strip(),
                "type": "comment"
            })
            continue
            
        # 处理键值对（可能带行末注释）
        if '=' in line:
            # 分离键值对和注释
            comment = ""
            if '#' in line:
                line_part, comment_part = line.split('#', 1)
                line = line_part.strip()
                comment = comment_part.strip()
            
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            
            # 移除值的引号，但保留数组格式
            if value.startswith('[') and value.endswith(']'):
                # 保留JSON数组格式
                pass
            elif (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            
            items.append({
                "key": key,
                "value": value,
                "comment": comment,
                "type": "keyvalue"
            })
        else:
            # 处理其他类型的行（可能是格式错误的行）
            items.append({
                "key": "",
                "value": "",
                "comment": line,
                "type": "other"
            })
    
    return items

def build_env_file(items: List[Dict[str, str]]) -> str:
    """从结构化数据构建 .env 文件内容"""
    lines = []
    
    for item in items:
        if item["type"] == "blank":
            lines.append("")
        elif item["type"] == "comment":
            lines.append(f"# {item['comment']}")
        elif item["type"] == "keyvalue":
            line = f"{item['key']}={item['value']}"
            if item["comment"]:
                line += f" # {item['comment']}"
            lines.append(line)
        elif item["type"] == "other":
            lines.append(item["comment"])
    
    return '\n'.join(lines)

def _get_nested(data: dict, dotted_key: str):
    """从嵌套dict中按点分隔的key取值，例如 'model.llm_reasoning' -> data['model']['llm_reasoning']"""
    keys = dotted_key.split('.')
    cur = data
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def parse_toml_file_with_comments(file_path: Path) -> Dict[str, Any]:
    """解析 TOML 文件，保留注释和原始结构"""
    if not file_path.exists():
        return {"sections": []}

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    try:
        # 预加载 TOML 数据以获取正确的数据类型
        data = toml.load(file_path)
    except toml.TomlDecodeError:
        data = {} # 如果文件格式错误，则优雅降级

    sections = []
    current_section_dict = None
    current_section_name = None
    array_table_counts = {}

    # 根级别的配置项
    root_section = {"name": "", "type": "root", "comment": "", "items": []}
    sections.append(root_section)
    current_section_dict = root_section
    last_was_blank = False  # 追踪上一行是否为空行

    for line in lines:
        stripped_line = line.strip()

        if not stripped_line:
            # 避免连续空行，只保留一个
            if not last_was_blank and current_section_dict:
                current_section_dict["items"].append({"type": "blank"})
            last_was_blank = True
            continue
        
        last_was_blank = False

        # 纯注释行
        if stripped_line.startswith('#'):
            comment = stripped_line[1:].strip()
            if current_section_dict:
                current_section_dict["items"].append({"type": "comment", "comment": comment})
            continue

        # Section 头
        if stripped_line.startswith('['):
            is_array_table = stripped_line.startswith('[[')
            end_bracket = ']]' if is_array_table else ']'
            
            try:
                name_part = stripped_line[len('[[' if is_array_table else '['):stripped_line.rindex(end_bracket)]
                comment_part = stripped_line[stripped_line.rindex(end_bracket) + len(end_bracket):].strip()
                inline_comment = comment_part[1:].strip() if comment_part.startswith('#') else ""
                
                current_section_name = name_part.strip()
                
                if is_array_table:
                    count = array_table_counts.get(current_section_name, 0)
                    current_section_dict = {
                        "name": current_section_name,
                        "type": "array_table",
                        "comment": inline_comment,
                        "items": [],
                        "_data_source": _get_nested(data, current_section_name, index=count)
                    }
                    array_table_counts[current_section_name] = count + 1
                else:
                    current_section_dict = {
                        "name": current_section_name,
                        "type": "section",
                        "comment": inline_comment,
                        "items": [],
                        "_data_source": _get_nested(data, current_section_name)
                    }
                sections.append(current_section_dict)

            except ValueError:
                # 解析 Section 头失败，当作一个普通注释或值处理
                if current_section_dict:
                     current_section_dict["items"].append({"type": "comment", "comment": stripped_line})
                continue
            continue

        # 键值对
        if '=' in stripped_line and current_section_dict:
            key, *value_parts = stripped_line.split('=', 1)
            key = key.strip()
            
            # 从预加载的数据中获取准确的值
            data_source = current_section_dict.get("_data_source", {})
            if isinstance(data_source, dict) and key in data_source:
                actual_value = data_source[key]
            else:
                # 如果无法从预加载数据获取，尝试从原始值解析
                if value_parts:
                    raw_value = value_parts[0].split('#')[0].strip()
                    actual_value = raw_value
                else:
                    actual_value = ""

            # 提取行内注释
            inline_comment = ""
            if value_parts:
                raw_value_str = value_parts[0].strip()
                if '#' in raw_value_str:
                    # 简单处理：找到第一个 # 后的内容作为注释
                    comment_pos = raw_value_str.find('#')
                    inline_comment = raw_value_str[comment_pos + 1:].strip()

            current_section_dict["items"].append({
                "key": key,
                "value": actual_value,
                "comment": inline_comment,
                "type": "keyvalue"
            })
    
    # 添加根级别配置项（不在任何 section 下的键值对）
    if isinstance(data, dict):
        for key, value in data.items():
            # 只添加不是嵌套字典或列表的简单键值对
            if not isinstance(value, (dict, list)) or (isinstance(value, list) and all(not isinstance(item, dict) for item in value)):
                # 检查是否已经在某个section中
                found_in_section = False
                for section in sections:
                    if section.get("type") in ["section", "array_table"]:
                        for item in section.get("items", []):
                            if item.get("key") == key:
                                found_in_section = True
                                break
                        if found_in_section:
                            break
                
                if not found_in_section:
                    root_section["items"].append({
                        "key": key,
                        "value": value,
                        "comment": "",
                        "type": "keyvalue"
                    })

    # 清理临时数据
    for section in sections:
        section.pop("_data_source", None)

    # 如果根section没有任何items，移除它
    if not root_section["items"]:
        sections.remove(root_section)

    return {"sections": sections}


def _get_nested(data: dict, key_str: str, index: int = -1):
    """从嵌套dict中按点分隔的key取值，例如 'model.llm_reasoning' -> data['model']['llm_reasoning']
    如果指定了 index >= 0，并且最终结果是一个数组，则返回数组中对应索引的元素"""
    if not key_str:
        return data
    
    keys = key_str.split('.')
    cur = data
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return {}
        cur = cur[k]
    
    # 如果指定了index并且当前值是列表，返回对应索引的项
    if index >= 0 and isinstance(cur, list):
        if index < len(cur):
            return cur[index]
        else:
            return {}
    
    return cur


# --- Pydantic 模型 ---
class EnvConfig(BaseModel):
    raw_content: str

class EnvConfigParsed(BaseModel):
    items: List[Dict[str, str]]  # [{"key": "HOST", "value": "127.0.0.1", "comment": ""}]

class TomlConfig(BaseModel):
    data: Dict[str, Any]

class TomlConfigParsed(BaseModel):
    sections: List[Dict[str, Any]]  # [{"name": "section", "items": [...], "comment": ""}]

class BotProcess:
    def __init__(self):
        self.process = None
        self.is_running = False
        self.log_history = []  
        self._read_task = None  # 新增：用于保存读取日志的异步任务，方便安全退出

    async def start(self):
        if self.is_running:
            return
        
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["LOGURU_COLORIZE"] = "1"
            env["FORCE_COLOR"] = "1"
            
            # 启动 bot.py 的命令策略：
            # 1) 优先使用环境变量 BOT_START_CMD（例如: "uv run" 或 "poetry run python"）
            #    当项目需要通过工具启动时，可以在 .env 中配置该值。
            # 2) 否则使用当前 Python 解释器启动（sys.executable），确保与当前虚拟环境一致。
            import shlex

            bot_start_cmd = os.getenv("BOT_START_CMD", "")
            if bot_start_cmd:
                # 将用户提供的命令拆分，并追加脚本路径
                try:
                    parts = shlex.split(bot_start_cmd)
                    cmd = parts + [BOT_SCRIPT]
                except Exception:
                    # fallback to simple split
                    cmd = bot_start_cmd.split() + [BOT_SCRIPT]
            else:
                # 使用当前 Python 解释器来启动 bot.py，确保子进程使用相同的虚拟环境
                cmd = [sys.executable, BOT_SCRIPT]
            
            # --- 新增：跨平台进程树配置 ---
            kwargs = {}
            if platform.system() != "Windows":
                # 在 Linux 下创建一个新的进程组(Session)
                kwargs["start_new_session"] = True  
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                **kwargs # <--- 传进去
            )
            self.is_running = True
            self.log_history = []
            
            self._read_task = asyncio.create_task(self._read_output())
            
            await manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "message": "机器人已启动"
            })
            
        except Exception as e:
            error_msg = f"启动失败: {str(e)}"
            print(error_msg)
            await manager.broadcast({
                "type": "bot_status", 
                "status": "error",
                "message": error_msg
            })

    async def stop(self):
        if not self.process:
            return
            
        self.is_running = False
        
        # 取消并等待 _read_task 真正结束，避免悬挂的后台任务阻止 uvicorn 关闭
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self._read_task = None
            
        try:
            if self.process.returncode is None:
                if platform.system() == "Windows":
                    # Windows：用 taskkill 强制杀死进程树
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                        stdout=subprocess.DEVNULL, 
                        stderr=subprocess.DEVNULL
                    )
                else:
                    # Linux：通过 killpg 对整个进程组发送 SIGTERM 优雅退出
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # 如果5秒内还没死透，Linux 下补一刀 SIGKILL 强制绝杀
                    if platform.system() != "Windows":
                        try:
                            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await self.process.wait()
        except Exception as e:
            print(f"停止子进程时出错: {e}")
        finally:
            if self.process and hasattr(self.process, '_transport') and self.process._transport:
                self.process._transport.close()
            self.process = None
            
        try:
            await manager.broadcast({
                "type": "bot_status",
                "status": "stopped", 
                "message": "机器人已停止"
            })
        except:
            pass

    async def restart(self):
        await self.stop()
        # 稍微多等一会儿，确保 Windows 操作系统完全释放套接字端口
        await asyncio.sleep(2) 
        await self.start()

    async def _read_output(self):
        if not self.process:
            return
            
        try:
            while self.is_running and self.process:
                try:
                    # 加超时，避免 readline() 永久阻塞导致任务无法被取消
                    line = await asyncio.wait_for(
                        self.process.stdout.readline(), timeout=1.0
                    )
                    if line:
                        try:
                            raw_output = line.decode('utf-8')
                        except UnicodeDecodeError:
                            raw_output = line.decode('gbk', errors='replace')
                            
                        sys.stdout.write(raw_output)
                        sys.stdout.flush()

                        clean_output = raw_output.strip()
                        if clean_output:
                            self.log_history.append(clean_output)
                            if len(self.log_history) > 200:
                                self.log_history.pop(0)
                            await manager.broadcast({
                                "type": "bot_output",
                                "output": clean_output
                            })
                    else:
                        # 子进程 stdout 已关闭（进程退出）
                        break
                except asyncio.TimeoutError:
                    # 1秒内没有新输出，继续循环检查 is_running 标志
                    continue
                except asyncio.CancelledError:
                    raise  # 向上传递，让 stop() 的 await 正常结束
                except Exception as e:
                    print(f"读取输出流错误: {e}")
                    break
        finally:
            self.is_running = False
            try:
                await manager.broadcast({
                    "type": "bot_status",
                    "status": "stopped",
                    "message": "机器人进程已退出"
                })
            except Exception:
                pass

bot_process = BotProcess()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        
        # 建立连接时，先推送历史日志给刚打开的前端
        for log_line in bot_process.log_history:
            await websocket.send_json({
                "type": "bot_output",
                "output": log_line
            })
            
        # 推送当前机器人状态
        await websocket.send_json({
            "type": "bot_status",
            "status": "running" if bot_process.is_running else "stopped"
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections.copy():
            try:
                await connection.send_json(message)
            except:
                self.disconnect(connection)

manager = ConnectionManager()


# 使用 Lifespan 替代废弃的 on_event
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动 WebUI 时，直接 await 启动 bot（不用 create_task，避免悬挂任务）
    await bot_process.start()
    yield
    # 关闭 WebUI 时，确保 Bot 被结束
    await bot_process.stop()

app = FastAPI(title="Aibot WebUI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件无缓存中间件（开发阶段避免 JS/CSS 更新后浏览器仍用旧版）
from starlette.middleware.base import BaseHTTPMiddleware

class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheStaticMiddleware)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- 认证接口 ---
@app.post("/api/login/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    # 使用直接的字符串比较，而不是哈希验证
    if not (form_data.username == WEBUI_USERNAME and form_data.password == WEBUI_PASSWORD):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": WEBUI_USERNAME}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

@app.get("/api/config/env")
async def get_env_config(current_user: str = Depends(get_current_user)):
    try:
        if ENV_FILE.exists():
            with open(ENV_FILE, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            return {"success": True, "raw_content": raw_content}
        else:
            return {"success": True, "raw_content": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/env/parsed")
async def get_env_config_parsed(current_user: str = Depends(get_current_user)):
    try:
        if ENV_FILE.exists():
            with open(ENV_FILE, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            items = parse_env_file(raw_content)
            return {"success": True, "items": items}
        else:
            return {"success": True, "items": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/env")
async def update_env_config(current_user: str = Depends(get_current_user), config: EnvConfig = Body(...)):
    try:
        with open(ENV_FILE, 'w', encoding='utf-8') as f:
            f.write(config.raw_content)
        await manager.broadcast({"type": "config_update", "file": ".env", "message": ".env文件已更新"})
        return {"success": True, "message": ".env文件已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/env/parsed")
async def update_env_config_parsed(current_user: str = Depends(get_current_user), config: EnvConfigParsed = Body(...)):
    try:
        # 结构化模式：前端传来的 items 已经是完整的当前状态，直接重建写入
        # 不能再读文件合并，否则注释会每次保存都重复追加
        new_raw_content = build_env_file(config.items)
        
        with open(ENV_FILE, 'w', encoding='utf-8') as f:
            f.write(new_raw_content)
        
        await manager.broadcast({"type": "config_update", "file": ".env", "message": ".env文件已更新"})
        return {"success": True, "message": ".env文件已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/toml")
async def get_toml_config(current_user: str = Depends(get_current_user)):
    try:
        if TOML_FILE.exists():
            with open(TOML_FILE, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            return {"success": True, "raw_content": raw_content}
        else:
            return {"success": True, "raw_content": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/toml/parsed")
async def get_toml_config_parsed(current_user: str = Depends(get_current_user)):
    try:
        if TOML_FILE.exists():
            # 尝试解析 TOML 文件
            parsed_data = parse_toml_file_with_comments(TOML_FILE)
            return {"success": True, "sections": parsed_data["sections"]}
        else:
            return {"success": True, "sections": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/toml")
async def update_toml_config(current_user: str = Depends(get_current_user), config: EnvConfig = Body(...)):
    try:
        # 直接写入原始 TOML 文本，保留注释和格式
        with open(TOML_FILE, 'w', encoding='utf-8') as f:
            f.write(config.raw_content)
        
        await manager.broadcast({"type": "config_update", "file": "bot_config.toml", "message": "bot_config.toml 文件已更新"})
        return {"success": True, "message": "bot_config.toml 文件已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/toml/parsed")
async def update_toml_config_parsed(current_user: str = Depends(get_current_user), config: TomlConfigParsed = Body(...)):
    try:
        lines = []
        for section_idx, section in enumerate(config.sections):
            sec_type = section.get("type", "section")

            # 纯注释行（section 级别）
            if sec_type == "comment":
                lines.append(f"# {section.get('comment', '')}")
                continue

            # 根级别 section 不添加 section 标题
            if sec_type == "root":
                pass  # 根级别项目直接处理，不添加标题
            # 普通 section
            elif sec_type == "section" and section.get("name"):
                # 在 section 前添加空行（除了第一个），但如果上一行是注释，则不要插入额外空行
                if lines and lines[-1] != "" and not lines[-1].lstrip().startswith('#'):
                    lines.append("")
                inline = f"  # {section['comment']}" if section.get("comment") else ""
                lines.append(f"[{section['name']}]{inline}")
            # 数组表格 [[name]]
            elif sec_type == "array_table" and section.get("name"):
                # 在 array_table 前添加空行（除了第一个），但如果上一行是注释，则不要插入额外空行
                if lines and lines[-1] != "" and not lines[-1].lstrip().startswith('#'):
                    lines.append("")
                inline = f"  # {section['comment']}" if section.get("comment") else ""
                lines.append(f"[[{section['name']}]]{inline}")

            last_was_blank = False
            for item in section.get("items", []):
                item_type = item.get("type", "keyvalue")
                
                if item_type == "comment":
                    lines.append(f"# {item.get('comment', '')}")
                    last_was_blank = False
                    continue
                    
                if item_type == "blank":
                    # 只在不是连续空行时添加
                    if not last_was_blank:
                        lines.append("")
                    last_was_blank = True
                    continue
                    
                last_was_blank = False
                # 只有 keyvalue 类型才处理成键值对
                if item_type == "keyvalue":
                    key = item.get("key", "")
                    value = item.get("value", "")
                    comment = item.get("comment", "")
                    
                    # 跳过空键的项（避免生成无效的 = "" 行）
                    if not key:
                        continue
                        
                    # 将值序列化为合法 TOML 字面量
                    if isinstance(value, bool):
                        toml_val = "true" if value else "false"
                    elif isinstance(value, (int, float)):
                        toml_val = str(value)
                    elif isinstance(value, list):
                        # 把列表里每个元素也序列化
                        def _toml_scalar(v):
                            if isinstance(v, bool):
                                return "true" if v else "false"
                            elif isinstance(v, (int, float)):
                                return str(v)
                            elif isinstance(v, str):
                                escaped = v.replace('\\', '\\\\').replace('"', '\\"')
                                return f'"{escaped}"'
                            return str(v)
                        toml_val = "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
                    elif isinstance(value, str):
                        # 直接转义并包引号，不走 toml.dumps 避免二次序列化
                        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                        toml_val = f'"{escaped}"'
                    else:
                        toml_val = str(value)
                    inline_comment = f"  # {comment}" if comment else ""
                    lines.append(f"{key} = {toml_val}{inline_comment}")

        new_content = "\n".join(lines)
        with open(TOML_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)

        await manager.broadcast({"type": "config_update", "file": "bot_config.toml", "message": "bot_config.toml 文件已更新"})
        return {"success": True, "message": "bot_config.toml 文件已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/control")
async def control_bot(action: str, current_user: str = Depends(get_current_user)):
    if action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    try:
        if action == "start":
            await bot_process.start()
            return {"success": True, "message": "机器人已启动"}
        elif action == "stop":
            await bot_process.stop()
            return {"success": True, "message": "机器人已停止"}
        elif action == "restart":
            await bot_process.restart()
            return {"success": True, "message": "机器人已重启"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/start")
async def start_bot(current_user: str = Depends(get_current_user)):
    try:
        await bot_process.start()
        return {"success": True, "message": "机器人已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/stop")
async def stop_bot(current_user: str = Depends(get_current_user)):
    try:
        await bot_process.stop()
        return {"success": True, "message": "机器人已停止"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/restart")
async def restart_bot(current_user: str = Depends(get_current_user)):
    try:
        await bot_process.restart()
        return {"success": True, "message": "机器人已重启"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bot/status")
async def get_bot_status(current_user: str = Depends(get_current_user)):
    return {
        "running": bot_process.is_running,
        "log_history": bot_process.log_history
    }

@app.get("/api/docs", include_in_schema=False)
async def get_swagger_ui_html():
    return FileResponse("static/docs/index.html")

@app.get("/api/logs")
async def get_logs():
    """获取日志文件内容"""
    try:
        log_file_path = Path("logs") / "bot.log"
        if log_file_path.exists():
            with open(log_file_path, 'r', encoding='utf-8') as f:
                logs = f.readlines()
            # 只返回最近的 100 行
            return {"success": True, "logs": logs[-100:]}
        else:
            return {"success": False, "message": "日志文件不存在"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.websocket("/api/logs/ws")
async def log_websocket(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        log_file_path = Path("logs") / "bot.log"
        if log_file_path.exists():
            # 发送历史日志
            with open(log_file_path, 'r', encoding='utf-8') as f:
                logs = f.readlines()
                for log in logs[-100:]:
                    await websocket.send_text(log.strip())
        
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        # 正常的客户端断开，不需要打印错误
        pass
    except asyncio.CancelledError:
        # 服务器关闭时的取消操作，不需要打印错误
        pass
    except RuntimeError as e:
        # 捕获 "Cannot call 'receive' once a disconnect message has been received" 等错误
        if "disconnect message" in str(e):
            pass  # 静默处理这类连接已断开的错误
        else:
            print(f"WebSocket RuntimeError: {e}")
    except Exception as e:
        print(f"WebSocket 错误: {e}")
    finally:
        manager.disconnect(websocket)

@app.get("/api/clear_logs")
async def clear_logs(current_user: str = Depends(get_current_user)):
    try:
        log_file_path = Path("logs") / "bot.log"
        if log_file_path.exists():
            os.remove(log_file_path)
            return {"success": True, "message": "日志文件已清除"}
        else:
            return {"success": False, "message": "日志文件不存在"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/test")
async def test_endpoint():
    return {"message": "测试成功"}

# 处理 WebSocket 连接
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # 用 receive() 代替 sleep，客户端断开或服务器关闭时都能立即退出
        while True:
            await websocket.receive()
    except WebSocketDisconnect:
        # 正常的客户端断开，不需要打印错误
        pass
    except asyncio.CancelledError:
        # 服务器关闭时的取消操作，不需要打印错误
        pass
    except RuntimeError as e:
        # 捕获 "Cannot call 'receive' once a disconnect message has been received" 等错误
        if "disconnect message" in str(e):
            pass  # 静默处理这类连接已断开的错误
        else:
            print(f"WebSocket RuntimeError: {e}")
    except Exception as e:
        print(f"WebSocket 错误: {e}")
    finally:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("WEBUI_PORT", "8088"))
    host = os.getenv("WEBUI_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)