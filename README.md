# Telegram Helper  
全新构建

## 客户端+Bot模式
无图形化界面，适合部署在服务器中  
简单易部署

## Ubuntu22+启动办法：
首先申请 telegram api_id, api_hash  
修改 config 中的参数

安装 tmux：
sudo apt install tmux

创建 tmux 会话：
tmux new -s mybot

运行你的 Python：
python3 telegram.py

按着提示登陆号账号输入验证码，输入 2fa

退出 tmux（不影响运行）：
Ctrl + B，然后 D

重新进入：
tmux attach -t mybot

关闭会话：
tmux kill-session -t mybot

---

# 5.0 客户端功能（新增 AI 炒群）

## 📖 命令帮助

### 🔧 基础命令:
- /help - 显示此帮助信息
- /status - 查看机器人状态
- /pause - 暂停所有功能
- /resume - 恢复所有功能

### 🤖 机器人交互:
- /start <@机器人> - 向机器人发送 /start
- /send <@机器人> <消息> - 向机器人发送消息

### 📢 频道管理:
- /join <链接或ID> - 加入群组/频道
- /leave <链接或ID> - 退出群组/频道

### 🔗 转发监听:
- /add_listen <源聊天> <@目标> - 添加监听
- /remove_listen <源聊天> - 移除监听
- /list_listen - 列出所有监听

### 🤖 AI 炒群:
- /ai on - 全局开启 AI 炒群
- /ai off - 全局关闭 AI 炒群
- /ai add <群组ID> - 添加炒群群组
- /ai remove <群组ID> - 移除炒群群组
- /ai list - 列出炒群群组
- /ai prob <概率> - 设置回复概率(0-100)
- /ai cooldown <秒> - 设置冷却时间
- /ai personality <人设> - 设置 AI 人设
- /ai status - 查看 AI 炒群状态
- /ai test <消息> - 测试 AI 回复
- /ai apikey <key> - 设置 API Key
- /ai baseurl <url> - 设置 API 地址
- /ai model <model> - 设置模型

### 📊 其他:
- /myid - 获取您的用户ID
- /chatid - 获取聊天ID

---

# 4.0 转发机器人（新增 AI 重写、伪原创、过滤广告）端启动：

与 BotFather 交互获取你的 bot_token  
修改 config 中的参数

安装 tmux：
sudo apt install tmux

创建 tmux 会话：
tmux new -s abot

运行你的 Python：
python3 bot.py

按提示登陆账号、输入验证码、输入 2FA

退出 tmux（不影响运行）：
Ctrl + B，然后 D

重新进入：
tmux attach -t mybot

关闭会话：
tmux kill-session -t mybot

---

# 4.0 客户端功能
/start 启动机器人，显示欢迎信息和用户ID  
/help 显示完整帮助信息  
/getid 获取用户/频道/群组ID (核心功能)  
/status 查看机器人运行状态  
/stats 查看详细转发统计  
/admin 打开管理面板  

---

# 1.0 监听机器人（由转发机器人魔改）启动：

与 BotFather 获取 bot_token  
修改 config 中的参数

安装 tmux：
sudo apt install tmux

创建 tmux 会话：
tmux new -s abot

运行 Python：
python3 keybot.py

登录账号、验证码、2FA

退出 tmux：
Ctrl + B，然后 D

重新进入：
tmux attach -t bbot

关闭会话：
tmux kill-session -t bbot

---

# 1.0 客户端功能
完全由按钮交互

---

# 使用教程：

## 1. 半自动：
仅配置同步 4.0 机器人  
实现手动转发给机器人 → 机器人同步其余目标频道

## 2. 全自动：
配置 5.0 客户端 和 4.0 客户端  
5.0 监听频道新消息 → 转发给 4.0 → 广告过滤 + AI 重写 + 伪原创 → 自动同步  
实现 “别人给你打工，他发广告也没用”

## 3. 监听：
配置 5.0 客户端 + 1.0 客户端  
5.0 进群获取消息 → 转发给 1.0  
1.0 处理后推送给自己或群聊  
实现自由监听每一个群的消息

## 4. AI 炒群：
仅配置 5.0 客户端  
配置 config 中的 ai 参数  
参考各大 AI 的 API 文档即可

---

# 注意事项：
- 使用过程中尽量使用双向老号（一年以上）或会员号  
- 使用稳定 IP  
- 避免账号消失造成财产损失
