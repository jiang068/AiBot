// Minbot WebUI JavaScript
class MinbotUI {
    constructor() {
        this.ws = null;
        this.envConfig = {};
        this.tomlConfig = {};
        this.isConnected = false;
        this.token = localStorage.getItem('minbot_token');
        
        // 编辑模式状态
        this.envEditMode = 'structured'; // 'structured' 或 'raw'
        this.tomlEditMode = 'structured'; // 'structured' 或 'raw'
        
        // 结构化数据存储
        this.envItems = [];
        this.tomlSections = [];

        // 控制台离屏时的日志缓冲（存 HTMLElement）
        this.pendingLogs = [];
        
        this.ansiUp = new AnsiUp();

        this.initTheme();
        this.bindEvents();
        // 异步检查认证状态
        this.checkAuth().catch(console.error);
    }

    async checkAuth() {
        if (this.token) {
            try {
                // 验证token是否仍然有效
                await this.apiRequest('/api/bot/status');
                this.showMainConsole(); // Token有效，显示主界面
            } catch (error) {
                console.warn('Token已过期或无效, 请重新登录。', error);
                this.handleLogout(); // Token无效，执行登出
            }
        } else {
            this.showLoginPanel(); // 没有token，显示登录界面
        }
    }

    showLoginPanel() {
        const loginPanel = document.getElementById('login-panel');
        const mainConsole = document.getElementById('main-console');
        
        if (loginPanel) {
            loginPanel.className = 'd-flex align-items-center justify-content-center vh-100 show-login';
            loginPanel.style.display = '';
        }
        if (mainConsole) {
            mainConsole.className = 'hidden';
            mainConsole.style.display = '';
        }
    }

    showMainConsole(force = false) {
        const loginPanel = document.getElementById('login-panel');
        const mainConsole = document.getElementById('main-console');
        
        const wasHidden = mainConsole && mainConsole.classList.contains('hidden');

        if (loginPanel) {
            loginPanel.className = 'hidden';
            loginPanel.style.display = 'none';
        }
        if (mainConsole) {
            mainConsole.className = 'show-main';
            mainConsole.style.display = '';
        }
        
        // 只有在从隐藏状态切换到显示状态，或者强制刷新时，才进行初始化
        if (wasHidden || force) {
            this.initWebSocket();
            this.loadConfigs();
        }
    }

    // WebSocket 连接
    initWebSocket() {
        if (this.ws) {
            this.ws.close();
        }
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws?token=${this.token}`;
        
        this.connectWebSocket(wsUrl);
    }

    connectWebSocket(url) {
        try {
            this.ws = new WebSocket(url);
            
            this.ws.onopen = () => {
                this.isConnected = true;
                this.updateConnectionStatus('已连接', 'success');
                this.getBotStatus();
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            };

            this.ws.onclose = (event) => {
                this.isConnected = false;
                this.updateConnectionStatus('连接断开', 'danger');
                
                // 如果是认证错误（1008），不要自动重连
                if (event.code === 1008) {
                    console.warn('WebSocket认证失败，可能需要重新登录');
                    return;
                }
                
                // 5秒后重连
                setTimeout(() => this.connectWebSocket(url), 5000);
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket错误:', error);
                this.updateConnectionStatus('连接错误', 'danger');
            };
        } catch (error) {
            console.error('WebSocket连接失败:', error);
            this.updateConnectionStatus('连接失败', 'danger');
        }
    }

    // 处理WebSocket消息
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'bot_status':
                this.updateBotStatus(data.status);
                // 只有报错的时候才弹出通知框，日常启停不弹
                if (data.status === 'error') {
                    this.showNotification(data.message, 'error');
                }
                break;
            case 'bot_output':
                this.appendLog(data.output);
                break;
            case 'config_update':
                this.showNotification(`${data.file} ${data.message}`);
                break;
            default:
                console.log('未知消息类型:', data);
        }
    }

    // 绑定事件
    bindEvents() {
        // 登录表单
        document.getElementById('login-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleLogin();
        });

        // 登出按钮
        document.getElementById('logoutButton').addEventListener('click', () => this.handleLogout());

        // 主题切换按钮
        document.getElementById('themeToggle').addEventListener('click', () => this.toggleTheme());

        // 机器人控制按钮
        document.getElementById('startBot').addEventListener('click', () => this.startBot());
        document.getElementById('stopBot').addEventListener('click', () => this.stopBot());
        document.getElementById('restartBot').addEventListener('click', () => this.restartBot());

        // 配置保存按钮
        document.getElementById('saveEnvConfig').addEventListener('click', () => this.saveEnvConfig());
        document.getElementById('saveTomlConfig').addEventListener('click', () => this.saveTomlConfig());
        document.getElementById('reloadConfigs').addEventListener('click', () => this.loadConfigs());

        // 添加配置项 / 分组按钮
        document.getElementById('addEnvItem').addEventListener('click', () => this.addEnvItem());
        document.getElementById('addTomlSection').addEventListener('click', () => this.addTomlSection());
        document.getElementById('addTomlItem').addEventListener('click', () => this.addTomlItem());

        // 清空日志
        document.getElementById('clearLogs').addEventListener('click', () => this.clearLogs());

        // 顶层页面 Tab 切换：切到控制台时刷新缓冲日志
        document.getElementById('page-console-tab').addEventListener('shown.bs.tab', () => {
            this.flushPendingLogs();
        });
        // 切到配置页时加载配置
        document.getElementById('page-config-tab').addEventListener('shown.bs.tab', () => {
            this.loadConfigs();
        });

        // 配置子标签页切换时重新加载对应配置
        document.getElementById('env-tab').addEventListener('shown.bs.tab', () => this.loadEnvConfig());
        document.getElementById('toml-tab').addEventListener('shown.bs.tab', () => this.loadTomlConfig());

        // .env 编辑模式切换（结构化 / 原始）
        document.querySelectorAll('input[name="envEditMode"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                this.switchEnvEditMode(e.target.id === 'envStructured' ? 'structured' : 'raw');
            });
        });

        // TOML 编辑模式切换（结构化 / 原始）
        document.querySelectorAll('input[name="tomlEditMode"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                this.switchTomlEditMode(e.target.id === 'tomlStructured' ? 'structured' : 'raw');
            });
        });
    }

    // 检测当前是否在控制台 Tab
    isConsolePage() {
        const tab = document.getElementById('page-console-tab');
        return tab && tab.classList.contains('active');
    }

    // 将缓冲的日志一次性刷入控制台
    flushPendingLogs() {
        if (this.pendingLogs.length === 0) return;
        const logOutput = document.getElementById('logOutput');
        if (!logOutput) return;
        this.pendingLogs.forEach(el => logOutput.appendChild(el));
        this.pendingLogs = [];
        logOutput.scrollTop = logOutput.scrollHeight;
        // 隐藏 badge
        const badge = document.getElementById('pendingLogsbadge');
        if (badge) badge.style.display = 'none';
    }

    // --- 认证相关 ---
    async handleLogin() {
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const errorEl = document.getElementById('login-error');
        errorEl.style.display = 'none';

        try {
            const formData = new URLSearchParams();
            formData.append('username', username);
            formData.append('password', password);

            const response = await fetch('/api/login/token', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || '登录失败');
            }

            const data = await response.json();
            this.token = data.access_token;
            localStorage.setItem('minbot_token', this.token);

            // 登录成功后，直接显示主控制台并重新初始化所有内容
            // 这可以避免checkAuth的异步调用带来的复杂性
            this.showMainConsole(true); // 传入true表示强制重新初始化

        } catch (error) {
            errorEl.textContent = error.message;
            errorEl.style.display = 'block';
        }
    }

    handleLogout() {
        this.token = null;
        localStorage.removeItem('minbot_token');
        if (this.ws) {
            this.ws.close();
        }
        // 直接显示登录面板，不使用异步的checkAuth
        this.showLoginPanel();
    }

    // API 请求封装
    async apiRequest(url, options = {}) {
        if (!this.token) {
            this.handleLogout();
            throw new Error('用户未认证');
        }
        try {
            const { headers: optionHeaders, ...restOptions } = options;
            const response = await fetch(url, {
                ...restOptions,
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.token}`,
                    ...optionHeaders
                },
            });
            
            if (response.status === 401) {
                this.handleLogout();
                throw new Error('认证已过期，请重新登录');
            }

            if (!response.ok) {
                // 尝试读取错误响应的内容
                let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
                try {
                    const contentType = response.headers.get('content-type');
                    if (contentType && contentType.includes('application/json')) {
                        const errorData = await response.json();
                        errorMessage = errorData.detail || errorData.message || errorMessage;
                    }
                } catch (e) {
                    // 如果无法解析错误响应，使用默认消息
                    console.warn('无法解析错误响应:', e);
                }
                throw new Error(errorMessage);
            }
            
            // 针对 204 No Content 等情况
            if (response.status === 204) {
                return null;
            }

            // 检查响应内容类型
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                console.error('API返回了非JSON响应:', contentType);
                const textContent = await response.text();
                console.error('响应内容:', textContent.substring(0, 200));
                throw new Error('服务器返回了意外的响应格式');
            }

            return await response.json();
        } catch (error) {
            console.error('API请求错误:', error);
            this.showNotification(`请求失败: ${error.message}`, 'error');
            throw error;
        }
    }

    // 加载所有配置
    async loadConfigs() {
        await Promise.all([
            this.loadEnvConfig(),
            this.loadTomlConfig()
        ]);
    }

    // 编辑模式切换
    switchEnvEditMode(mode) {
        this.envEditMode = mode;
        const structuredEditor = document.getElementById('envStructuredEditor');
        const rawEditor = document.getElementById('envRawEditor');
        const hint = document.getElementById('envEditorHint');
        
        if (mode === 'structured') {
            structuredEditor.style.display = 'block';
            rawEditor.style.display = 'none';
            hint.textContent = '结构化编辑模式：分条目管理配置';
            this.renderEnvStructuredEditor();
        } else {
            structuredEditor.style.display = 'none';
            rawEditor.style.display = 'block';
            hint.textContent = '原始文件编辑模式：直接编辑原始格式';
        }
    }

    switchTomlEditMode(mode) {
        this.tomlEditMode = mode;
        const structuredEditor = document.getElementById('tomlStructuredEditor');
        const rawEditor = document.getElementById('tomlRawEditor');
        const hint = document.getElementById('tomlEditorHint');
        
        if (mode === 'structured') {
            structuredEditor.style.display = 'block';
            rawEditor.style.display = 'none';
            hint.textContent = '结构化编辑模式：分组和条目管理';
            this.renderTomlStructuredEditor();
        } else {
            structuredEditor.style.display = 'none';
            rawEditor.style.display = 'block';
            hint.textContent = '原始文件编辑模式：直接编辑TOML语法';
        }
    }

    // 加载 .env 配置
    async loadEnvConfig() {
        try {
            // 同时加载原始内容和解析后的结构化数据
            const [rawResult, parsedResult] = await Promise.all([
                this.apiRequest('/api/config/env'),
                this.apiRequest('/api/config/env/parsed')
            ]);
            
            // 检查响应格式
            if (!rawResult || typeof rawResult !== 'object') {
                throw new Error('原始配置响应格式错误');
            }
            if (!parsedResult || typeof parsedResult !== 'object') {
                throw new Error('解析配置响应格式错误');
            }
            
            // 更新原始编辑器
            const envEditor = document.getElementById('envEditor');
            if (envEditor) {
                envEditor.value = rawResult.raw_content || '';
            }
            
            // 更新结构化数据
            this.envItems = Array.isArray(parsedResult.items) ? parsedResult.items : [];
            
            // 根据当前模式显示对应编辑器
            if (this.envEditMode === 'structured') {
                this.renderEnvStructuredEditor();
            }
        } catch (error) {
            console.error('加载 .env 配置失败:', error);
            const envEditor = document.getElementById('envEditor');
            if (envEditor) {
                envEditor.value = '# 加载配置失败: ' + error.message;
            }
            this.envItems = [];
            if (this.envEditMode === 'structured') {
                this.renderEnvStructuredEditor();
            }
        }
    }

    // 加载 TOML 配置
    async loadTomlConfig() {
        try {
            // 同时加载原始内容和解析后的结构化数据
            const [rawResult, parsedResult] = await Promise.all([
                this.apiRequest('/api/config/toml'),
                this.apiRequest('/api/config/toml/parsed')
            ]);
            
            // 检查响应格式
            if (!rawResult || typeof rawResult !== 'object') {
                throw new Error('原始TOML配置响应格式错误');
            }
            if (!parsedResult || typeof parsedResult !== 'object') {
                throw new Error('解析TOML配置响应格式错误');
            }
            
            this.tomlConfig = rawResult.raw_content || '';
            
            // 直接将原始 TOML 文本填入编辑器
            const tomlEditor = document.getElementById('tomlEditor');
            if (tomlEditor) {
                tomlEditor.value = this.tomlConfig;
            }
            // 更新结构化数据
            this.tomlSections = Array.isArray(parsedResult.sections) ? parsedResult.sections : [];
            
            // 根据当前模式显示对应编辑器
            if (this.tomlEditMode === 'structured') {
                this.renderTomlStructuredEditor();
            }
        } catch (error) {
            console.error('加载 TOML 配置失败:', error);
            const tomlEditor = document.getElementById('tomlEditor');
            if (tomlEditor) {
                tomlEditor.value = '# 加载配置失败: ' + error.message;
            }
            this.tomlSections = [];
            if (this.tomlEditMode === 'structured') {
                this.renderTomlStructuredEditor();
            }
        }
    }

    // 保存 .env 配置
    async saveEnvConfig() {
        try {
            if (this.envEditMode === 'structured') {
                // 结构化模式：提交结构化数据
                await this.apiRequest('/api/config/env/parsed', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ items: this.envItems })
                });
            } else {
                // 原始模式：提交原始文本
                const envText = document.getElementById('envEditor').value;
                await this.apiRequest('/api/config/env', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ raw_content: envText })
                });
            }
            
            this.showNotification('.env 配置已保存', 'success');
        } catch (error) {
            this.showNotification('保存 .env 配置失败', 'error');
        }
    }

    // 保存 TOML 配置
    async saveTomlConfig() {
        try {
            if (this.tomlEditMode === 'structured') {
                // 结构化模式：提交结构化数据
                await this.apiRequest('/api/config/toml/parsed', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ sections: this.tomlSections })
                });
            } else {
                // 原始模式：直接提交原始 TOML 文本
                const tomlText = document.getElementById('tomlEditor').value;
                await this.apiRequest('/api/config/toml', {
                    method: 'POST',
                    body: JSON.stringify({ raw_content: tomlText })
                });
            }
            
            this.showNotification('TOML 配置已保存', 'success');
        } catch (error) {
            this.showNotification('保存 TOML 配置失败', 'error');
        }
    }

    // 机器人控制
    async startBot() {
        this.updateBotStatus('starting');
        try {
            const result = await this.apiRequest('/api/bot/start', { method: 'POST' });
            if (!result.success) {
                this.showNotification(result.message, 'error');
                this.getBotStatus();
            }
        } catch (error) {
            this.showNotification('启动机器人请求失败', 'error');
            this.getBotStatus();
        }
    }

    async stopBot() {
        this.updateBotStatus('stopping');
        try {
            const result = await this.apiRequest('/api/bot/stop', { method: 'POST' });
            if (!result.success) {
                this.showNotification(result.message, 'error');
                this.getBotStatus();
            }
        } catch (error) {
            this.showNotification('停止机器人请求失败', 'error');
            this.getBotStatus();
        }
    }

    async restartBot() {
        this.updateBotStatus('restarting'); 
        try {
            const result = await this.apiRequest('/api/bot/restart', { method: 'POST' });
            if (!result.success) {
                this.showNotification(result.message, 'error');
                this.getBotStatus();
            }
        } catch (error) {
            this.showNotification('重启机器人请求失败', 'error');
            this.getBotStatus();
        }
    }

    // 获取机器人状态
    async getBotStatus() {
        try {
            const result = await this.apiRequest('/api/bot/status');
            if (result.success) {
                this.updateBotStatus(result.status);
            }
        } catch (error) {
            console.error('获取机器人状态失败:', error);
            this.updateBotStatus('unknown');
        }
    }

    // UI 更新方法：增加对按钮外观和动画的控制
    updateBotStatus(status) {
        const element = document.getElementById('botStatus');
        const statusMap = {
            'running': { text: '运行中', class: 'bg-success' },
            'stopped': { text: '已停止', class: 'bg-danger' },
            'starting': { text: '启动中', class: 'bg-warning text-dark' },
            'stopping': { text: '停止中', class: 'bg-warning text-dark' },
            'restarting': { text: '重启中', class: 'bg-warning text-dark' },
            'error': { text: '出错了', class: 'bg-danger' },
            'unknown': { text: '未知', class: 'bg-secondary' }
        };
        
        const statusInfo = statusMap[status] || statusMap['unknown'];
        if (element) {
            element.textContent = statusInfo.text;
            element.className = `badge bot-status-badge ${statusInfo.class}`;
        }

        const startBtn = document.getElementById('startBot');
        const stopBtn = document.getElementById('stopBot');
        const restartBtn = document.getElementById('restartBot');

        // 1. 每次更新状态前，先重置所有按钮的默认图标和文字
        startBtn.innerHTML = '<i class="bi bi-play-fill"></i><span class="ctrl-label"> 启动</span>';
        stopBtn.innerHTML = '<i class="bi bi-stop-fill"></i><span class="ctrl-label"> 停止</span>';
        restartBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i><span class="ctrl-label"> 重启</span>';

        // Bootstrap 的转圈圈动画 HTML
        const spinner = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

        // 2. 根据状态控制逻辑
        if (status === 'running') {
            startBtn.disabled = true;
            stopBtn.disabled = false;
            restartBtn.disabled = false;
        } else if (status === 'stopped' || status === 'error') {
            startBtn.disabled = false;
            stopBtn.disabled = true;
            restartBtn.disabled = true;
        } else {
            // 过渡状态：锁死所有按钮防误触
            startBtn.disabled = true;
            stopBtn.disabled = true;
            restartBtn.disabled = true;

            // 给当前正在执行的按钮换上转圈动画和提示文字
            if (status === 'starting') {
                startBtn.innerHTML = spinner + '<span class="ctrl-label"> 启动中</span>';
            } else if (status === 'stopping') {
                stopBtn.innerHTML = spinner + '<span class="ctrl-label"> 停止中</span>';
            } else if (status === 'restarting') {
                restartBtn.innerHTML = spinner + '<span class="ctrl-label"> 重启中</span>';
            }
        }
    }

    // 解析环境变量文本
    parseEnvText(text) {
        const envData = {};
        const lines = text.split('\n');
        
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
                const [key, ...valueParts] = trimmed.split('=');
                envData[key.trim()] = valueParts.join('=').trim();
            }
        }
        
        return envData;
    }

    // 简单的TOML文本解析（实际项目中建议使用专门的TOML库）
    parseTomlText(text) {
        try {
            // 这里是一个简化的TOML解析，实际项目中应该使用专门的库
            // 由于浏览器环境限制，这里只做基本解析
            const lines = text.split('\n');
            const result = {};
            let currentSection = result;
            let currentSectionName = '';

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('#')) continue;

                // 检查是否是节标题
                if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
                    currentSectionName = trimmed.slice(1, -1);
                    currentSection = result[currentSectionName] = {};
                    continue;
                }

                // 解析键值对
                if (trimmed.includes('=')) {
                    const [key, ...valueParts] = trimmed.split('=');
                    let value = valueParts.join('=').trim();

                    // 简单的类型转换
                    if (value.startsWith('"') && value.endsWith('"')) {
                        value = value.slice(1, -1);
                    } else if (value === 'true') {
                        value = true;
                    } else if (value === 'false') {
                        value = false;
                    } else if (!isNaN(value)) {
                        value = parseFloat(value);
                    } else if (value.startsWith('[') && value.endsWith(']')) {
                        try {
                            value = JSON.parse(value);
                        } catch (e) {
                            // 保持原始字符串
                        }
                    }

                    currentSection[key.trim()] = value;
                }
            }

            return result;
        } catch (error) {
            console.error('TOML解析错误:', error);
            throw new Error('TOML格式错误');
        }
    }

    // 对象转TOML字符串
    objectToTomlString(obj, indent = '') {
        let result = '';
        
        for (const [key, value] of Object.entries(obj)) {
            if (typeof value === 'object' && !Array.isArray(value) && value !== null) {
                result += `${indent}[${key}]\n`;
                result += this.objectToTomlString(value, '');
                result += '\n';
            } else {
                let valueStr = value;
                if (typeof value === 'string') {
                    valueStr = `"${value}"`;
                } else if (Array.isArray(value)) {
                    valueStr = JSON.stringify(value);
                }
                result += `${indent}${key} = ${valueStr}\n`;
            }
        }
        
        return result;
    }

    // UI 更新方法
    updateConnectionStatus(status, type) {
        const element = document.getElementById('connectionStatus');
        element.textContent = status;
        element.className = `badge bg-${type === 'success' ? 'success' : type === 'danger' ? 'danger' : 'secondary'}`;
    }

    appendLog(message) {
        // 确保message是字符串且不包含恶意HTML
        const safeMessage = String(message).trim();
        if (!safeMessage) return;
        
        // 使用 ansi_up 把终端颜色代码转换为网页带颜色的 HTML
        const htmlMessage = this.ansiUp.ansi_to_html(safeMessage);
        
        // 创建新的div元素而不是使用innerHTML
        const logDiv = document.createElement('div');
        logDiv.style.marginBottom = '2px';
        logDiv.innerHTML = htmlMessage;

        // 若当前不在控制台页面，暂存到缓冲，并更新 badge 提示
        if (!this.isConsolePage()) {
            this.pendingLogs.push(logDiv);
            const badge = document.getElementById('pendingLogsbadge');
            if (badge) {
                badge.style.display = '';
                badge.textContent = this.pendingLogs.length > 99 ? '99+' : String(this.pendingLogs.length);
            }
            return;
        }

        const logOutput = document.getElementById('logOutput');
        if (!logOutput) return;
        logOutput.appendChild(logDiv);
        logOutput.scrollTop = logOutput.scrollHeight;
    }

    clearLogs() {
        this.pendingLogs = [];
        const badge = document.getElementById('pendingLogsbadge');
        if (badge) badge.style.display = 'none';
        const logOutput = document.getElementById('logOutput');
        if (logOutput) logOutput.innerHTML = '';
    }

    showNotification(message, type = 'info') {
        const toast = document.getElementById('notificationToast');
        if (!toast) return;
        const body = toast.querySelector('.toast-body');
        if (body) body.textContent = message;

        // 清理旧类型类并设置新的，CSS 根据类型显示不同颜色
        toast.classList.remove('toast-info', 'toast-success', 'toast-error');
        if (type === 'success') toast.classList.add('toast-success');
        else if (type === 'error') toast.classList.add('toast-error');
        else toast.classList.add('toast-info');

        const toastInstance = bootstrap.Toast.getOrCreateInstance(toast, { delay: 3500 });
        toastInstance.show();
    }

    // 主题相关方法
    initTheme() {
        // 从 localStorage 读取保存的主题设置，默认为亮色主题
        const savedTheme = localStorage.getItem('theme') || 'light';
        this.setTheme(savedTheme);
    }

    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        this.setTheme(newTheme);
    }

    setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        
        // 更新主题切换按钮图标
        const themeIcon = document.querySelector('#themeToggle i');
        if (theme === 'dark') {
            themeIcon.className = 'bi bi-moon-fill';
        } else {
            themeIcon.className = 'bi bi-sun-fill';
        }
    }

    // === 结构化编辑器渲染方法 ===
    
    // 渲染 .env 结构化编辑器
    renderEnvStructuredEditor() {
        const container = document.getElementById('envItemsList');
        if (!container) {
            console.error('envItemsList container not found');
            return;
        }
  
        container.innerHTML = '';
        
        if (!this.envItems || this.envItems.length === 0) {
            container.innerHTML = '<div class="text-center text-muted p-3">暂无配置项</div>';
            return;
        }
        
        this.envItems.forEach((item, index) => {
            try {
                const itemElement = this.createEnvItemElement(item, index);
                if (itemElement) {
                    container.appendChild(itemElement);
                }
            } catch (error) {
                console.error('创建环境变量配置项时出错:', error, item);
            }
        });
    }
    
    // 创建单个 .env 配置项元素
    createEnvItemElement(item, index) {
        const div = document.createElement('div');
        div.className = `config-item ${item.type === 'comment' ? 'comment-item' : ''}`;
        
        // 安全地处理数据，防止HTML注入
        const safeKey = (item.key || '').replace(/[<>&"']/g, (match) => {
            const map = {'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;'};
            return map[match];
        });
        const safeValue = (item.value || '').replace(/[<>&"']/g, (match) => {
            const map = {'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;'};
            return map[match];
        });
        const safeComment = (item.comment || '').replace(/[<>&"']/g, (match) => {
            const map = {'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;'};
            return map[match];
        });
        
        if (item.type === 'comment') {
            div.className = 'config-comment';
            div.innerHTML = `
                <div class="comment-text">
                    # ${safeComment}
                </div>
            `;
        } else if (item.type === 'blank') {
            // 空行：不显示任何内容，但保留在数据中以维持文件格式
            div.className = 'config-blank';
            div.innerHTML = '';
        } else {
            div.innerHTML = `
                <div class="config-item-header">
                    <h6 class="config-item-title">${safeKey || '新配置项'}</h6>
                    <div class="config-item-actions">
                        <button class="btn btn-outline-danger btn-xs" onclick="ui.removeEnvItem(${index})">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="config-item-body">
                    <div class="form-group">
                        <label>配置键</label>
                        <input type="text" value="${safeKey}" 
                               onchange="ui.updateEnvItem(${index}, 'key', this.value)"
                               placeholder="例如: HOST">
                    </div>
                    <div class="form-group">
                        <label>配置值</label>
                        <input type="text" value="${safeValue}"
                               onchange="ui.updateEnvItem(${index}, 'value', this.value)"
                               placeholder="例如: 127.0.0.1">
                        ${item.comment ? `
                        <small class="text-muted mt-1 d-block"># ${safeComment}</small>
                        ` : ''}
                    </div>
                </div>
            `;
        }
        
        return div;
    }
    
    // 渲染 TOML 结构化编辑器
    renderTomlStructuredEditor() {
        const container = document.getElementById('tomlSectionsList');
        if (!container) {
            console.error('tomlSectionsList container not found');
            return;
        }
        
        container.innerHTML = '';
        
        if (!this.tomlSections || this.tomlSections.length === 0) {
            container.innerHTML = '<div class="text-center text-muted p-3">暂无配置分组</div>';
            return;
        }
        
        this.tomlSections.forEach((section, index) => {
            try {
                const sectionElement = this.createTomlSectionElement(section, index);
                if (sectionElement) {
                    container.appendChild(sectionElement);
                }
            } catch (error) {
                console.error('创建TOML配置分组时出错:', error, section);
            }
        });
    }
    
    // 创建单个 TOML 分组元素
    createTomlSectionElement(section, sectionIndex) {
        // section 级别的纯注释行（不属于任何 [section]）
        if (section.type === 'comment') {
            const div = document.createElement('div');
            div.className = 'config-comment';
            const safeComment = (section.comment || '').replace(/[<>&"']/g, m => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));
            div.innerHTML = `<div class="comment-text"># ${safeComment}</div>`;
            return div;
        }

        // 创建分组卡片
        const div = document.createElement('div');
        div.className = 'config-section-item';

        const sectionTitle = section.name || (section.type === 'root' ? '全局配置' : '未命名分组');
        const isArrayTable = section.type === 'array_table';
        const titleClass = section.type === 'root' ? 'section-title root-section' : 'section-title';
        const titleDisplay = isArrayTable ? `[[${sectionTitle}]]` : `[${sectionTitle}]`;

        // 不再把组间注释内嵌在 header 中，改为在外部单独渲染（便于区分组注释与组内项注释）
        div.innerHTML = `
            <div class="config-section-header">
                <h5 class="${titleClass}">${titleDisplay}</h5>
                <div class="section-actions">
                    ${section.type !== 'root' ? `
                    <button class="btn btn-outline-primary btn-xs" onclick="ui.editTomlSectionName(${sectionIndex})">
                        <i class="bi bi-pencil"></i> 编辑
                    </button>
                    <button class="btn btn-outline-danger btn-xs" onclick="ui.removeTomlSection(${sectionIndex})">
                        <i class="bi bi-trash"></i> 删除
                    </button>
                    ` : ''}
                </div>
            </div>
            <div class="config-section-body">
                <div id="tomlSectionItems-${sectionIndex}" class="section-items">
                    <!-- 配置项将由JavaScript动态生成 -->
                </div>
                <div class="section-add-item" onclick="ui.addTomlItemToSection(${sectionIndex})">
                    <i class="bi bi-plus"></i> 添加配置项到此分组
                </div>
                ${isArrayTable ? `
                <div class="section-add-item" style="border-top:1px dashed var(--border-color);margin-top:4px" onclick="ui.addTomlArrayTableEntry(${sectionIndex})">
                    <i class="bi bi-plus-square"></i> 新增一条 [[${sectionTitle}]] 条目
                </div>` : ''}
            </div>
        `;

        const fragment = document.createDocumentFragment();
        const safeGroupComment = (section.comment || '').replace(/[<>&"']/g, (m) => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));

        const itemsContainer = div.querySelector(`#tomlSectionItems-${sectionIndex}`);
        const items = Array.isArray(section.items) ? section.items : [];

        // Find first and last keyvalue indices
        let firstKV = -1;
        let lastKV = -1;
        for (let i = 0; i < items.length; i++) {
            if (items[i].type === 'keyvalue') {
                if (firstKV === -1) firstKV = i;
                lastKV = i;
            }
        }

        if (firstKV === -1) {
            // No key/value inside this section: keep all item-level comments inside the section
            // but still render explicit section.comment (if any) outside above the section
            if (safeGroupComment) {
                const groupCommentEl = document.createElement('div');
                groupCommentEl.className = 'config-comment';
                groupCommentEl.innerHTML = `<div class="comment-text"># ${safeGroupComment}</div>`;
                fragment.appendChild(groupCommentEl);
            }

            items.forEach((item, itemIndex) => {
                if (item.type === 'comment') {
                    const commentEl = document.createElement('div');
                    commentEl.className = 'config-comment';
                    const safeComment = (item.comment || '').replace(/[<>&"']/g, m => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));
                    commentEl.innerHTML = `<div class="comment-text"># ${safeComment}</div>`;
                    itemsContainer.appendChild(commentEl);
                } else if (item.type === 'keyvalue') {
                    const itemElement = this.createTomlItemElement(item, sectionIndex, itemIndex);
                    itemsContainer.appendChild(itemElement);
                }
            });

            fragment.appendChild(div);
            return fragment;
        }

        // There are key/value items: move leading comments before firstKV to outside (above),
        // and trailing comments after lastKV to outside (below). Keep inner comments inside.

        // leading comments
        for (let i = 0; i < firstKV; i++) {
            if (items[i].type === 'comment') {
                const commentEl = document.createElement('div');
                commentEl.className = 'config-comment';
                const safeComment = (items[i].comment || '').replace(/[<>&"']/g, m => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));
                commentEl.innerHTML = `<div class="comment-text"># ${safeComment}</div>`;
                fragment.appendChild(commentEl);
            }
        }

        // explicit section comment goes just above the section box
        if (safeGroupComment) {
            const groupCommentEl = document.createElement('div');
            groupCommentEl.className = 'config-comment';
            groupCommentEl.innerHTML = `<div class="comment-text"># ${safeGroupComment}</div>`;
            fragment.appendChild(groupCommentEl);
        }

        // render inner items inside the section
        for (let i = firstKV; i <= lastKV; i++) {
            const item = items[i];
            if (item.type === 'comment') {
                const commentEl = document.createElement('div');
                commentEl.className = 'config-comment';
                const safeComment = (item.comment || '').replace(/[<>&"']/g, m => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));
                commentEl.innerHTML = `<div class="comment-text"># ${safeComment}</div>`;
                itemsContainer.appendChild(commentEl);
            } else if (item.type === 'keyvalue') {
                const itemElement = this.createTomlItemElement(item, sectionIndex, i);
                itemsContainer.appendChild(itemElement);
            }
        }

        fragment.appendChild(div);

        // trailing comments
        for (let i = lastKV + 1; i < items.length; i++) {
            if (items[i].type === 'comment') {
                const commentEl = document.createElement('div');
                commentEl.className = 'config-comment';
                const safeComment = (items[i].comment || '').replace(/[<>&"']/g, m => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[m]));
                commentEl.innerHTML = `<div class="comment-text"># ${safeComment}</div>`;
                fragment.appendChild(commentEl);
            }
        }

        return fragment;
    }
    
    // 创建单个 TOML 配置项元素
    createTomlItemElement(item, sectionIndex, itemIndex) {
        const div = document.createElement('div');
        div.className = 'config-item';
        
        const formattedValue = this.formatTomlValue(item.value);
        const safeComment = (item.comment || '').replace(/[<>&"']/g, (match) => {
            const map = {'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;'};
            return map[match];
        });
        
        div.innerHTML = `
            <div class="config-item-header">
                <h6 class="config-item-title">${item.key || '新配置项'}</h6>
                <div class="config-item-actions">
                    <button class="btn btn-outline-danger btn-xs" onclick="ui.removeTomlItem(${sectionIndex}, ${itemIndex})">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
            <div class="config-item-body">
                <div class="form-group">
                    <label>配置键</label>
                    <input type="text" value="${item.key || ''}" 
                           onchange="ui.updateTomlItem(${sectionIndex}, ${itemIndex}, 'key', this.value)"
                           placeholder="例如: timeout">
                </div>
                <div class="form-group">
                    <label>配置值</label>
                    <textarea rows="2" onchange="ui.updateTomlItem(${sectionIndex}, ${itemIndex}, 'value', ui.parseTomlValue(this.value))"
                              placeholder='例如: 30 或 ["item1", "item2"] 或 "text value"'>${formattedValue}</textarea>
                    ${safeComment ? `<small class="text-muted mt-1 d-block"># ${safeComment}</small>` : ''}
                </div>
            </div>
        `;
        
        return div;
    }
    
    // === 编辑操作方法 ===
    
    // .env 编辑操作
    addEnvItem() {
        this.envItems.push({
            key: '',
            value: '',
            comment: '',
            type: 'keyvalue'
        });
        this.renderEnvStructuredEditor();
    }
    
    removeEnvItem(index) {
        this.envItems.splice(index, 1);
        this.renderEnvStructuredEditor();
    }
    
    updateEnvItem(index, field, value) {
        if (this.envItems[index]) {
            this.envItems[index][field] = value;
        }
    }
    
    // TOML 编辑操作
    addTomlSection() {
        const name = prompt('请输入分组名称:');
        if (name && name.trim()) {
            this.tomlSections.push({
                name: name.trim(),
                type: 'section',
                comment: '',
                items: []
            });
            this.renderTomlStructuredEditor();
        }
    }

    // 在现有 [[array_table]] 末尾追加一个同名新条目
    addTomlArrayTableEntry(sectionIndex) {
        const section = this.tomlSections[sectionIndex];
        if (!section || section.type !== 'array_table') return;
        // 找到最后一个同名 array_table 的位置，插到它后面
        let lastIdx = sectionIndex;
        for (let i = sectionIndex; i < this.tomlSections.length; i++) {
            if (this.tomlSections[i].type === 'array_table' && this.tomlSections[i].name === section.name) {
                lastIdx = i;
            }
        }
        this.tomlSections.splice(lastIdx + 1, 0, {
            name: section.name,
            type: 'array_table',
            comment: '',
            items: []
        });
        this.renderTomlStructuredEditor();
    }
    
    addTomlItem() {
        // 添加到根级别
        let rootSection = this.tomlSections.find(s => s.type === 'root');
        if (!rootSection) {
            rootSection = {
                name: '',
                type: 'root',
                comment: '',
                items: []
            };
            this.tomlSections.unshift(rootSection);
        }
        
        rootSection.items.push({
            key: '',
            value: '',
            comment: '',
            type: 'keyvalue'
        });
        this.renderTomlStructuredEditor();
    }
    
    addTomlItemToSection(sectionIndex) {
        if (this.tomlSections[sectionIndex]) {
            this.tomlSections[sectionIndex].items.push({
                key: '',
                value: '',
                comment: '',
                type: 'keyvalue'
            });
            this.renderTomlStructuredEditor();
        }
    }
    
    removeTomlSection(sectionIndex) {
        if (confirm('确定要删除这个分组及其所有配置项吗？')) {
            this.tomlSections.splice(sectionIndex, 1);
            this.renderTomlStructuredEditor();
        }
    }
    
    removeTomlItem(sectionIndex, itemIndex) {
        if (this.tomlSections[sectionIndex] && this.tomlSections[sectionIndex].items[itemIndex]) {
            this.tomlSections[sectionIndex].items.splice(itemIndex, 1);
            this.renderTomlStructuredEditor();
        }
    }
    
    updateTomlItem(sectionIndex, itemIndex, field, value) {
        if (this.tomlSections[sectionIndex] && this.tomlSections[sectionIndex].items[itemIndex]) {
            this.tomlSections[sectionIndex].items[itemIndex][field] = value;
        }
    }
    
    editTomlSectionName(sectionIndex) {
        const section = this.tomlSections[sectionIndex];
        if (section) {
            const newName = prompt('请输入新的分组名称:', section.name);
            if (newName !== null && newName.trim()) {
                section.name = newName.trim();
                this.renderTomlStructuredEditor();
            }
        }
    }
    
    // === 工具方法 ===
    
    formatTomlValue(value) {
        // 将后端传来的 Python 原生值格式化为 TOML 原文，直接展示在 textarea 中。
        // 用户在 textarea 里看到/编辑的就是合法 TOML 字面量，保存时 parseTomlValue 再解析回来。
        if (typeof value === 'boolean') {
            return value ? 'true' : 'false';
        }
        if (value === null || value === undefined) {
            return '""';
        }
        if (typeof value === 'number') {
            return String(value);
        }
        if (Array.isArray(value)) {
            // 把数组里每个元素也序列化为 TOML 字面量
            const items = value.map(v => {
                if (typeof v === 'string') return `"${v.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
                if (typeof v === 'boolean') return v ? 'true' : 'false';
                return String(v);
            });
            return `[${items.join(', ')}]`;
        }
        if (typeof value === 'object') {
            // 内联表：简单 JSON 展示
            return JSON.stringify(value);
        }
        if (typeof value === 'string') {
            if (value.includes('\n')) {
                // 多行字符串
                return `"""\n${value}\n"""`;
            }
            // 普通字符串：始终加引号，转义内部反斜杠和双引号
            return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
        }
        return String(value);
    }
    
    parseTomlValue(str) {
        const trimmed = str.trim();

        // 空字符串
        if (trimmed === '') return '';

        // 多行字符串 """..."""
        if (trimmed.startsWith('"""') && trimmed.endsWith('"""')) {
            return trimmed.slice(3, -3);
        }

        // 带双引号的字符串 "..." → 解码 TOML 转义序列
        if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
            const inner = trimmed.slice(1, -1);
            // 解码 TOML 基本字符串转义
            return inner
                .replace(/\\"/g, '"')
                .replace(/\\\\/g, '\\')
                .replace(/\\n/g, '\n')
                .replace(/\\t/g, '\t')
                .replace(/\\r/g, '\r');
        }

        // 单引号字面字符串 '...' → 不处理转义
        if (trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2) {
            return trimmed.slice(1, -1);
        }

        // 布尔值
        if (trimmed === 'true') return true;
        if (trimmed === 'false') return false;

        // 数字
        if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
            return parseFloat(trimmed);
        }

        // 数组 [...]
        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
            try {
                // 用 JSON.parse 尝试（兼容大多数 TOML 数组写法）
                const jsonCompatible = trimmed.replace(/'/g, '"');
                return JSON.parse(jsonCompatible);
            } catch (e) {
                // 手动解析逗号分隔
                const content = trimmed.slice(1, -1).trim();
                if (!content) return [];
                const items = content.split(',').map(item => this.parseTomlValue(item.trim()));
                return items;
            }
        }

        // 尝试 JSON 解析（内联表等）
        try {
            return JSON.parse(trimmed);
        } catch {
            // 兜底：当作裸字符串返回
            return trimmed;
        }
    }
}

// 全局变量，供HTML onclick事件使用
let ui;

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    ui = new MinbotUI();
});