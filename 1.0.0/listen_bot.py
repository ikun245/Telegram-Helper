# main.py - 5.0 关键词监听提醒机器人 (修复版)
# 功能: 接收 3. 0 转发来的消息，检测关键词并提醒管理员

import asyncio
import logging
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update, Message
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import sqlite3

# 获取脚本所在目录
SCRIPT_DIR = os.path. dirname(os.path.abspath(__file__))

# 版本信息
VERSION = "5.0. 1"
BANNER = f"""
╔══════════════════════════════════════════════════════════╗
║       Telegram 关键词监听提醒机器人 v{VERSION}              ║
║       接收转发消息 | 关键词检测 | 实时提醒管理员             ║
╚══════════════════════════════════════════════════════════╝
"""

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging. FileHandler(os.path.join(SCRIPT_DIR, 'keyword_bot.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def escape_markdown_v2(text: str) -> str:
    """转义 MarkdownV2 特殊字符"""
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


class KeywordMonitorBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.db_path = os.path.join(SCRIPT_DIR, "keyword_bot.db")
        self.config_file = os.path.join(SCRIPT_DIR, "keyword_config.json")

        self.init_database()
        self.config = self.load_config()

        self.stats = {
            'messages_received': 0,
            'keywords_matched': 0,
            'alerts_sent': 0,
            'start_time': datetime.now()
        }

        self.register_handlers()

    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS keyword_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT,
                message_text TEXT,
                source_chat_id INTEGER,
                source_chat_title TEXT,
                source_user_id INTEGER,
                source_username TEXT,
                forward_date TEXT,
                notified_admins TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def load_config(self) -> dict:
        """加载配置文件"""
        default_config = {
            "bot_token": "YOUR_BOT_TOKEN_HERE",
            "admins": [],
            "notify_users": [],
            "keywords": [],
            "keyword_rules": [],
            "settings": {
                "case_sensitive": False,
                "regex_enabled": False,
                "include_source_info": True,
                "alert_cooldown": 0,
                "max_message_length": 500,
            },
            "whitelist_chats": [],
            "blacklist_chats": [],
        }

        if os.path. exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    for key, value in default_config. items():
                        if key not in config:
                            config[key] = value
                        elif isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if sub_key not in config[key]:
                                    config[key][sub_key] = sub_value
                    return config
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
                return default_config
        else:
            self.save_config(default_config)
            return default_config

    def save_config(self, config: dict = None):
        """保存配置文件"""
        if config is None:
            config = self.config

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def register_handlers(self):
        """注册消息处理器"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application. add_handler(CommandHandler("help", self.help_command))
        self.application. add_handler(CommandHandler("getid", self.getid_command))
        self. application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("stats", self. stats_command))
        self. application.add_handler(CommandHandler("admin", self.admin_panel))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(
            filters.ALL & (~filters.COMMAND),
            self.handle_message
        ))

    async def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员"""
        return user_id in self.config. get("admins", [])

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """开始命令"""
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name

        welcome_text = f"""🔍 *欢迎使用关键词监听提醒机器人 v{VERSION}*

👤 *您的信息:*
• 用户名: {escape_markdown_v2(user_name)}
• 用户ID: `{user_id}`

📋 *主要功能:*
• 🔑 监听转发消息中的关键词
• 🔔 匹配时实时通知管理员
• 📊 显示消息来源详情
• 📈 统计关键词匹配情况

🔧 *快速开始:*
1\\. 使用 `/admin` 进入管理面板
2\\. 添加要监听的关键词
3\\. 设置接收提醒的用户
4\\. 将 3\\.0 客户端的目标机器人设置为本机器人

📖 输入 `/help` 查看所有命令"""

        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """帮助命令"""
        help_text = """📖 *命令列表*

🔧 *基础命令:*
• `/start` \\- 启动机器人
• `/help` \\- 显示此帮助信息
• `/getid` \\- 获取用户/频道ID
• `/status` \\- 查看机器人状态
• `/stats` \\- 查看匹配统计

⚙️ *管理命令 \\(仅管理员\\):*
• `/admin` \\- 打开管理面板

💡 *工作原理:*
1\\. 3\\.0 客户端监听源频道消息
2\\. 转发消息到本机器人
3\\. 本机器人检测关键词
4\\. 匹配时通知指定用户"""

        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def getid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取ID命令"""
        message = update.message
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        response_text = f"""🆔 *ID 信息*

👤 *您的用户ID:* `{user_id}`
💬 *当前聊天ID:* `{chat_id}`"""

        if message.reply_to_message:
            replied_msg = message.reply_to_message
            source_info = self._extract_source_info(replied_msg)
            response_text += self._format_source_info_for_display(source_info)

        response_text += """

💡 *提示:* 回复一条转发的消息可以获取详细来源信息"""

        await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN_V2)

    def _format_source_info_for_display(self, source_info: dict) -> str:
        """格式化来源信息用于显示"""
        info_text = ""

        if source_info.get('chat_title') or source_info.get('chat_id'):
            info_text += "\n\n📤 *转发来源 \\(频道/群组\\):*"
            if source_info.get('chat_id'):
                info_text += f"\n• ID: `{source_info['chat_id']}`"
            if source_info.get('chat_title'):
                info_text += f"\n• 名称: {escape_markdown_v2(source_info['chat_title'])}"
            if source_info.get('chat_username'):
                info_text += f"\n• 用户名: @{escape_markdown_v2(source_info['chat_username'])}"

        if source_info.get('user_id') or source_info.get('user_name'):
            info_text += "\n\n📤 *转发来源 \\(用户\\):*"
            if source_info.get('user_id'):
                info_text += f"\n• ID: `{source_info['user_id']}`"
            if source_info.get('user_name'):
                info_text += f"\n• 名称: {escape_markdown_v2(source_info['user_name'])}"
            if source_info.get('username'):
                info_text += f"\n• 用户名: @{escape_markdown_v2(source_info['username'])}"

        if source_info.get('sender_name') and not source_info.get('user_id'):
            info_text += f"\n\n📤 *转发来源 \\(隐藏用户\\):*\n• 名称: {escape_markdown_v2(source_info['sender_name'])}"

        if source_info.get('forward_date'):
            info_text += f"\n\n⏰ *原消息时间:* {escape_markdown_v2(source_info['forward_date'])}"

        if not info_text:
            info_text = "\n\nℹ️ 这不是一条转发的消息，或来源信息不可用"

        return info_text

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """状态命令"""
        user_id = update.effective_user. id

        uptime = datetime.now() - self.stats['start_time']
        uptime_str = str(uptime).split('.')[0]

        is_admin = await self.is_admin(user_id)
        is_notify_user = user_id in self.config.get('notify_users', [])

        status_text = f"""📊 *机器人状态*

🕐 *运行时间:* {escape_markdown_v2(uptime_str)}
📥 *接收消息:* {self.stats['messages_received']}
🔑 *关键词匹配:* {self.stats['keywords_matched']}
🔔 *发送提醒:* {self.stats['alerts_sent']}

⚙️ *配置信息:*
• 关键词数量: {len(self.config.get('keywords', []))}
• 关键词规则: {len(self.config.get('keyword_rules', []))}
• 管理员数量: {len(self.config.get('admins', []))}
• 提醒用户数: {len(self.config.get('notify_users', []))}

👤 *您的状态:*
• 管理员: {'✅' if is_admin else '❌'}
• 接收提醒: {'✅' if is_notify_user else '❌'}"""

        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def stats_command(self, update: Update, context: ContextTypes. DEFAULT_TYPE):
        """统计命令"""
        user_id = update.effective_user. id
        if not await self.is_admin(user_id):
            await update.message.reply_text("❌ 您没有权限查看统计信息")
            return

        conn = sqlite3.connect(self. db_path)
        cursor = conn.cursor()

        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT keyword, COUNT(*) as count
            FROM keyword_logs
            WHERE DATE(timestamp) = ? 
            GROUP BY keyword
            ORDER BY count DESC
            LIMIT 10
        ''', (today,))
        today_keywords = cursor.fetchall()

        cursor. execute('SELECT COUNT(*) FROM keyword_logs')
        total_matches = cursor.fetchone()[0]

        conn.close()

        stats_text = "📈 *关键词匹配统计*\n\n📅 *今日匹配的关键词 Top 10:*\n"
        if today_keywords:
            for i, (keyword, count) in enumerate(today_keywords, 1):
                stats_text += f"{i}\\. `{escape_markdown_v2(keyword)}`: {count}次\n"
        else:
            stats_text += "暂无数据\n"

        stats_text += f"\n📊 *总计:*\n• 历史匹配总数: {total_matches}"

        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def admin_panel(self, update: Update, context: ContextTypes. DEFAULT_TYPE):
        """管理面板"""
        user_id = update.effective_user. id
        if not await self. is_admin(user_id):
            await update.message.reply_text(
                f"❌ 您没有权限使用此机器人\n\n您的用户ID: `{user_id}`\n请联系管理员添加权限",
                parse_mode=ParseMode.MARKDOWN_V2)
            return
        await self.send_admin_panel(update. effective_chat.id, context)

    async def send_admin_panel(self, chat_id: int, context: ContextTypes. DEFAULT_TYPE):
        """发送主管理面板"""
        keyboard = [
            [InlineKeyboardButton("🔑 关键词管理", callback_data="keyword_menu")],
            [InlineKeyboardButton("👥 用户管理", callback_data="user_menu")],
            [InlineKeyboardButton("⚙️ 设置", callback_data="settings_menu")],
            [InlineKeyboardButton("📊 查看最近匹配", callback_data="recent_matches")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚙️ 关键词监听机器人管理面板 v{VERSION}",
            reply_markup=reply_markup
        )

    async def send_keyword_menu(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """关键词管理菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加关键词", callback_data="add_keyword_prompt")],
            [InlineKeyboardButton("📋 列出关键词", callback_data="list_keywords")],
            [InlineKeyboardButton("➖ 删除关键词", callback_data="remove_keyword_prompt")],
            [InlineKeyboardButton("📝 添加关键词规则", callback_data="add_keyword_rule_prompt")],
            [InlineKeyboardButton("📋 列出关键词规则", callback_data="list_keyword_rules")],
            [InlineKeyboardButton("➖ 删除关键词规则", callback_data="remove_keyword_rule_prompt")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔑 关键词管理\n\n• 全局关键词：匹配时通知所有提醒用户\n• 关键词规则：匹配时只通知指定用户",
            reply_markup=reply_markup
        )

    async def send_user_menu(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """用户管理菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加管理员", callback_data="add_admin_prompt")],
            [InlineKeyboardButton("📋 列出管理员", callback_data="list_admins")],
            [InlineKeyboardButton("➖ 移除管理员", callback_data="remove_admin_prompt")],
            [InlineKeyboardButton("➕ 添加提醒用户", callback_data="add_notify_user_prompt")],
            [InlineKeyboardButton("📋 列出提醒用户", callback_data="list_notify_users")],
            [InlineKeyboardButton("➖ 移除提醒用户", callback_data="remove_notify_user_prompt")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id,
            text="👥 用户管理\n\n• 管理员：可以管理机器人设置\n• 提醒用户：接收关键词匹配提醒",
            reply_markup=reply_markup
        )

    async def send_settings_menu(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """设置菜单"""
        settings = self.config.get('settings', {})
        case_text = "🟢 开启" if settings.get('case_sensitive') else "🔴 关闭"
        regex_text = "🟢 开启" if settings.get('regex_enabled') else "🔴 关闭"
        source_text = "🟢 开启" if settings.get('include_source_info') else "🔴 关闭"

        keyboard = [
            [InlineKeyboardButton(f"🔤 区分大小写 ({case_text})", callback_data="toggle_case_sensitive")],
            [InlineKeyboardButton(f"🔣 正则表达式 ({regex_text})", callback_data="toggle_regex")],
            [InlineKeyboardButton(f"📢 显示来源信息 ({source_text})", callback_data="toggle_source_info")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="⚙️ 设置", reply_markup=reply_markup)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """按钮回调处理"""
        query = update. callback_query
        await query. answer()
        user_id = query.from_user.id

        if not await self.is_admin(user_id):
            await query. edit_message_text(text="❌ 您没有权限")
            return

        data = query.data
        chat_id = query.message.chat_id

        # 菜单导航
        if data == "main_menu":
            await self. send_admin_panel(chat_id, context)
            return
        elif data == "keyword_menu":
            await self.send_keyword_menu(chat_id, context)
            context.user_data['last_menu'] = 'keyword_menu'
            return
        elif data == "user_menu":
            await self.send_user_menu(chat_id, context)
            context.user_data['last_menu'] = 'user_menu'
            return
        elif data == "settings_menu":
            await self.send_settings_menu(chat_id, context)
            context.user_data['last_menu'] = 'settings_menu'
            return

        # 输入提示
        input_prompts = {
            "add_keyword_prompt": ("请发送要添加的关键词\n\n💡 可以一次添加多个，每行一个", "add_keyword"),
            "remove_keyword_prompt": ("请发送要删除的关键词", "remove_keyword"),
            "add_keyword_rule_prompt": ("请发送关键词规则，格式:\n关键词 用户ID1 用户ID2 ...\n\n例如: 优惠 123456789 987654321", "add_keyword_rule"),
            "remove_keyword_rule_prompt": ("请发送要删除的关键词规则的关键词", "remove_keyword_rule"),
            "add_admin_prompt": ("请发送要添加的管理员用户ID", "add_admin"),
            "remove_admin_prompt": ("请发送要移除的管理员用户ID", "remove_admin"),
            "add_notify_user_prompt": ("请发送要添加的提醒用户ID", "add_notify_user"),
            "remove_notify_user_prompt": ("请发送要移除的提醒用户ID", "remove_notify_user"),
        }

        if data in input_prompts:
            prompt_text, action = input_prompts[data]
            await query.edit_message_text(text=prompt_text)
            context.user_data['awaiting_input'] = action
            return

        # 列表显示
        if data == "list_keywords":
            keywords = self.config.get('keywords', [])
            if not keywords:
                text = "🔑 当前没有配置关键词"
            else:
                text = "🔑 *全局关键词列表:*\n\n"
                for i, kw in enumerate(keywords, 1):
                    text += f"{i}\\. `{escape_markdown_v2(kw)}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode. MARKDOWN_V2)
            return

        if data == "list_keyword_rules":
            rules = self.config.get('keyword_rules', [])
            if not rules:
                text = "📝 当前没有配置关键词规则"
            else:
                text = "📝 *关键词规则列表:*\n\n"
                for i, rule in enumerate(rules, 1):
                    status = "✅" if rule.get('enabled', True) else "❌"
                    users = ', '.join(str(u) for u in rule. get('notify_users', []))
                    text += f"{i}\\.  {status} `{escape_markdown_v2(rule['keyword'])}` → \\[{users}\\]\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_admins":
            admins = self. config.get('admins', [])
            if not admins:
                text = "👥 当前没有配置管理员"
            else:
                text = "👥 *管理员列表:*\n\n"
                for i, admin_id in enumerate(admins, 1):
                    text += f"{i}\\. `{admin_id}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_notify_users":
            users = self.config.get('notify_users', [])
            if not users:
                text = "🔔 当前没有配置提醒用户"
            else:
                text = "🔔 *提醒用户列表:*\n\n"
                for i, uid in enumerate(users, 1):
                    text += f"{i}\\. `{uid}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode. MARKDOWN_V2)
            return

        if data == "recent_matches":
            conn = sqlite3.connect(self. db_path)
            cursor = conn.cursor()
            cursor. execute('''
                SELECT keyword, source_chat_title, message_text, timestamp
                FROM keyword_logs
                ORDER BY timestamp DESC
                LIMIT 10
            ''')
            matches = cursor.fetchall()
            conn.close()

            if not matches:
                text = "📊 暂无匹配记录"
            else:
                text = "📊 *最近10条匹配记录:*\n\n"
                for kw, chat_title, msg_text, ts in matches:
                    msg_preview = (msg_text[:50] + '... ') if msg_text and len(msg_text) > 50 else (msg_text or '无')
                    text += f"🔑 `{escape_markdown_v2(kw)}`\n"
                    text += f"📢 {escape_markdown_v2(chat_title or '未知')}\n"
                    text += f"💬 {escape_markdown_v2(msg_preview)}\n"
                    text += f"⏰ {escape_markdown_v2(ts)}\n\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        # 切换开关
        if data == "toggle_case_sensitive":
            self.config['settings']['case_sensitive'] = not self.config['settings']. get('case_sensitive', False)
            self.save_config()
            status = "开启" if self.config['settings']['case_sensitive'] else "关闭"
            await query. edit_message_text(text=f"✅ 区分大小写已{status}")
        elif data == "toggle_regex":
            self.config['settings']['regex_enabled'] = not self. config['settings'].get('regex_enabled', False)
            self. save_config()
            status = "开启" if self.config['settings']['regex_enabled'] else "关闭"
            await query.edit_message_text(text=f"✅ 正则表达式已{status}")
        elif data == "toggle_source_info":
            self.config['settings']['include_source_info'] = not self.config['settings'].get('include_source_info', True)
            self.save_config()
            status = "开启" if self.config['settings']['include_source_info'] else "关闭"
            await query.edit_message_text(text=f"✅ 显示来源信息已{status}")

        # 刷新面板
        await self._refresh_panel(chat_id, context)

    async def _refresh_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """刷新当前面板"""
        last_menu = context.user_data.get('last_menu', 'main_menu')
        if last_menu == 'keyword_menu':
            await self.send_keyword_menu(chat_id, context)
        elif last_menu == 'user_menu':
            await self.send_user_menu(chat_id, context)
        elif last_menu == 'settings_menu':
            await self.send_settings_menu(chat_id, context)
        else:
            await self.send_admin_panel(chat_id, context)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息"""
        message = update.message
        if not message:
            return

        user_id = message.from_user.id if message.from_user else None

        # 如果用户正在等待输入
        if user_id and await self.is_admin(user_id) and context.user_data.get('awaiting_input'):
            await self.handle_admin_input(update, context)
            return

        # 处理转发来的消息，检测关键词
        await self.process_forwarded_message(message)

    async def handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理管理员输入"""
        chat_id = update.effective_chat.id
        input_text = update.message.text
        action = context.user_data.pop('awaiting_input', None)

        if not action:
            return

        try:
            if action == 'add_keyword':
                keywords = [kw.strip() for kw in input_text.split('\n') if kw.strip()]
                added = []
                for kw in keywords:
                    if kw not in self.config['keywords']:
                        self.config['keywords']. append(kw)
                        added.append(kw)
                if added:
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加关键词:\n" + '\n'.join(f"• {k}" for k in added))
                else:
                    await context. bot.send_message(chat_id=chat_id, text="❌ 关键词已存在或无效")

            elif action == 'remove_keyword':
                kw = input_text.strip()
                if kw in self. config['keywords']:
                    self.config['keywords'].remove(kw)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已删除关键词: {kw}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 关键词不存在")

            elif action == 'add_keyword_rule':
                parts = input_text.strip().split()
                if len(parts) >= 2:
                    keyword = parts[0]
                    try:
                        notify_users = [int(uid) for uid in parts[1:]]
                        rule = {"keyword": keyword, "notify_users": notify_users, "enabled": True}
                        self.config['keyword_rules']. append(rule)
                        self.save_config()
                        await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加关键词规则:\n关键词: {keyword}\n通知用户: {notify_users}")
                    except ValueError:
                        await context.bot.send_message(chat_id=chat_id, text="❌ 用户ID格式错误")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 格式错误，请使用: 关键词 用户ID1 用户ID2 ...")

            elif action == 'remove_keyword_rule':
                kw = input_text.strip()
                original_len = len(self.config['keyword_rules'])
                self.config['keyword_rules'] = [r for r in self.config['keyword_rules'] if r['keyword'] != kw]
                if len(self.config['keyword_rules']) < original_len:
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已删除关键词规则: {kw}")
                else:
                    await context. bot.send_message(chat_id=chat_id, text="❌ 关键词规则不存在")

            elif action == 'add_admin':
                admin_id = int(input_text)
                if admin_id not in self.config['admins']:
                    self.config['admins'].append(admin_id)
                    self.save_config()
                    await context.bot. send_message(chat_id=chat_id, text=f"✅ 已添加管理员: {admin_id}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户已是管理员")

            elif action == 'remove_admin':
                admin_id = int(input_text)
                if admin_id in self.config['admins']:
                    self.config['admins'].remove(admin_id)
                    self.save_config()
                    await context. bot.send_message(chat_id=chat_id, text=f"✅ 已移除管理员: {admin_id}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户不是管理员")

            elif action == 'add_notify_user':
                uid = int(input_text)
                if uid not in self.config['notify_users']:
                    self.config['notify_users']. append(uid)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加提醒用户: {uid}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户已在提醒列表中")

            elif action == 'remove_notify_user':
                uid = int(input_text)
                if uid in self.config['notify_users']:
                    self. config['notify_users'].remove(uid)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除提醒用户: {uid}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户不在提醒列表中")

        except ValueError:
            await context.bot. send_message(chat_id=chat_id, text="❌ 输入格式错误")
        except Exception as e:
            logger.error(f"处理管理员输入失败: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"❌ 处理失败: {e}")
        finally:
            await self._refresh_panel(chat_id, context)

    async def process_forwarded_message(self, message: Message):
        """处理转发的消息，检测关键词"""
        self.stats['messages_received'] += 1

        # 获取消息文本
        text = message.text or message.caption or ""
        if not text:
            return

        # 获取来源信息
        source_info = self._extract_source_info(message)

        # 检测关键词
        matched_keywords = self._check_keywords(text)

        if matched_keywords:
            self.stats['keywords_matched'] += len(matched_keywords)
            logger.info(f"检测到关键词匹配: {[m['keyword'] for m in matched_keywords]}")
            await self._send_alerts(message, text, matched_keywords, source_info)

    def _extract_source_info(self, message: Message) -> dict:
        """提取消息来源信息 (兼容 python-telegram-bot 21.x)"""
        info = {
            'chat_id': None,
            'chat_title': None,
            'chat_username': None,
            'chat_type': None,
            'user_id': None,
            'user_name': None,
            'username': None,
            'sender_name': None,
            'forward_date': None,
            'message_id': message.message_id,
        }

        # python-telegram-bot 21.x 使用 forward_origin
        if hasattr(message, 'forward_origin') and message.forward_origin:
            origin = message.forward_origin
            origin_type = type(origin).__name__

            # MessageOriginChannel - 来自频道
            if origin_type == 'MessageOriginChannel':
                if hasattr(origin, 'chat'):
                    info['chat_id'] = origin.chat.id
                    info['chat_title'] = origin.chat.title
                    info['chat_username'] = getattr(origin.chat, 'username', None)
                    info['chat_type'] = origin.chat.type
                if hasattr(origin, 'date'):
                    info['forward_date'] = origin.date. strftime('%Y-%m-%d %H:%M:%S')

            # MessageOriginUser - 来自用户
            elif origin_type == 'MessageOriginUser':
                if hasattr(origin, 'sender_user'):
                    info['user_id'] = origin.sender_user.id
                    info['user_name'] = origin.sender_user.full_name
                    info['username'] = getattr(origin.sender_user, 'username', None)
                if hasattr(origin, 'date'):
                    info['forward_date'] = origin.date.strftime('%Y-%m-%d %H:%M:%S')

            # MessageOriginHiddenUser - 来自隐藏用户
            elif origin_type == 'MessageOriginHiddenUser':
                if hasattr(origin, 'sender_user_name'):
                    info['sender_name'] = origin.sender_user_name
                if hasattr(origin, 'date'):
                    info['forward_date'] = origin.date.strftime('%Y-%m-%d %H:%M:%S')

            # MessageOriginChat - 来自群组
            elif origin_type == 'MessageOriginChat':
                if hasattr(origin, 'sender_chat'):
                    info['chat_id'] = origin.sender_chat.id
                    info['chat_title'] = origin. sender_chat.title
                    info['chat_username'] = getattr(origin.sender_chat, 'username', None)
                    info['chat_type'] = origin.sender_chat.type
                if hasattr(origin, 'date'):
                    info['forward_date'] = origin.date.strftime('%Y-%m-%d %H:%M:%S')

            logger.info(f"提取到转发来源信息: {origin_type} -> {info}")

        # 兼容旧版本属性 (以防万一)
        else:
            if hasattr(message, 'forward_from_chat') and message.forward_from_chat:
                chat = message.forward_from_chat
                info['chat_id'] = chat.id
                info['chat_title'] = chat.title
                info['chat_username'] = getattr(chat, 'username', None)
                info['chat_type'] = chat.type

            if hasattr(message, 'forward_from') and message.forward_from:
                user = message.forward_from
                info['user_id'] = user.id
                info['user_name'] = user.full_name
                info['username'] = getattr(user, 'username', None)

            if hasattr(message, 'forward_sender_name') and message.forward_sender_name:
                info['sender_name'] = message.forward_sender_name

            if hasattr(message, 'forward_date') and message.forward_date:
                info['forward_date'] = message.forward_date.strftime('%Y-%m-%d %H:%M:%S')

        return info

    def _check_keywords(self, text: str) -> List[dict]:
        """检查文本中的关键词"""
        matched = []
        settings = self.config. get('settings', {})
        case_sensitive = settings.get('case_sensitive', False)
        regex_enabled = settings.get('regex_enabled', False)

        check_text = text if case_sensitive else text.lower()

        # 检查全局关键词
        for keyword in self.config. get('keywords', []):
            check_keyword = keyword if case_sensitive else keyword.lower()

            if regex_enabled:
                try:
                    if re.search(check_keyword, check_text):
                        matched.append({
                            'keyword': keyword,
                            'notify_users': self.config.get('notify_users', []),
                            'type': 'global'
                        })
                except re.error:
                    if check_keyword in check_text:
                        matched.append({
                            'keyword': keyword,
                            'notify_users': self. config.get('notify_users', []),
                            'type': 'global'
                        })
            else:
                if check_keyword in check_text:
                    matched.append({
                        'keyword': keyword,
                        'notify_users': self.config.get('notify_users', []),
                        'type': 'global'
                    })

        # 检查关键词规则
        for rule in self.config.get('keyword_rules', []):
            if not rule. get('enabled', True):
                continue

            keyword = rule['keyword']
            check_keyword = keyword if case_sensitive else keyword.lower()

            if regex_enabled:
                try:
                    if re.search(check_keyword, check_text):
                        matched.append({
                            'keyword': keyword,
                            'notify_users': rule.get('notify_users', []),
                            'type': 'rule'
                        })
                except re.error:
                    if check_keyword in check_text:
                        matched.append({
                            'keyword': keyword,
                            'notify_users': rule.get('notify_users', []),
                            'type': 'rule'
                        })
            else:
                if check_keyword in check_text:
                    matched.append({
                        'keyword': keyword,
                        'notify_users': rule.get('notify_users', []),
                        'type': 'rule'
                    })

        return matched

    async def _send_alerts(self, message: Message, text: str, matched_keywords: List[dict], source_info: dict):
        """发送提醒"""
        settings = self.config.get('settings', {})
        max_length = settings.get('max_message_length', 500)

        # 截断消息
        text_preview = text[:max_length] + '...' if len(text) > max_length else text

        # 收集所有需要通知的用户
        users_to_notify = set()
        keywords_str = []

        for match in matched_keywords:
            keywords_str.append(match['keyword'])
            for uid in match['notify_users']:
                users_to_notify.add(uid)

        if not users_to_notify:
            logger.warning("没有配置提醒用户，跳过发送提醒")
            return

        # 构建提醒消息
        alert_text = f"""🔔 *关键词匹配提醒*

🔑 *匹配关键词:* {', '.join(f'`{escape_markdown_v2(k)}`' for k in keywords_str)}

💬 *消息内容:*
{escape_markdown_v2(text_preview)}"""

        # 添加来源信息
        if settings.get('include_source_info', True):
            alert_text += "\n\n📢 *来源信息:*"

            if source_info. get('chat_title'):
                alert_text += f"\n• 频道/群组: {escape_markdown_v2(source_info['chat_title'])}"
            if source_info.get('chat_id'):
                alert_text += f"\n• 频道ID: `{source_info['chat_id']}`"
            if source_info.get('chat_username'):
                alert_text += f"\n• 频道用户名: @{escape_markdown_v2(source_info['chat_username'])}"

            if source_info.get('user_name'):
                alert_text += f"\n• 发送者: {escape_markdown_v2(source_info['user_name'])}"
            if source_info.get('user_id'):
                alert_text += f"\n• 用户ID: `{source_info['user_id']}`"
            if source_info.get('username'):
                alert_text += f"\n• 用户名: @{escape_markdown_v2(source_info['username'])}"
            if source_info.get('sender_name') and not source_info.get('user_id'):
                alert_text += f"\n• 发送者: {escape_markdown_v2(source_info['sender_name'])} \\(隐藏\\)"

            if source_info.get('forward_date'):
                alert_text += f"\n• 原消息时间: {escape_markdown_v2(source_info['forward_date'])}"

        alert_text += f"\n\n⏰ *检测时间:* {escape_markdown_v2(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"

        # 发送提醒
        notified = []
        for uid in users_to_notify:
            try:
                await self.application.bot.send_message(
                    chat_id=uid,
                    text=alert_text,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                notified.append(uid)
                self.stats['alerts_sent'] += 1
                logger.info(f"已发送关键词提醒到用户 {uid}")
            except Exception as e:
                logger.error(f"发送提醒到用户 {uid} 失败: {e}")

        # 记录日志
        self._log_match(matched_keywords, text, source_info, notified)

    def _log_match(self, matched_keywords: List[dict], text: str, source_info: dict, notified_users: List[int]):
        """记录匹配日志"""
        try:
            conn = sqlite3.connect(self. db_path)
            cursor = conn.cursor()

            for match in matched_keywords:
                cursor.execute('''
                    INSERT INTO keyword_logs 
                    (keyword, message_text, source_chat_id, source_chat_title, 
                     source_user_id, source_username, forward_date, notified_admins)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    match['keyword'],
                    text[:1000],
                    source_info. get('chat_id'),
                    source_info.get('chat_title'),
                    source_info.get('user_id'),
                    source_info.get('username'),
                    source_info.get('forward_date'),
                    json.dumps(notified_users)
                ))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录匹配日志失败: {e}")

    def run(self):
        """运行机器人"""
        print(BANNER)
        logger.info("关键词监听机器人启动中...")
        self.application.run_polling()


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "keyword_config.json")

    print(f"📁 脚本目录: {script_dir}")
    print(f"📁 配置文件: {config_file}")

    TOKEN = None

    if not os.path.exists(config_file):
        print(f"❌ 配置文件不存在，正在创建默认配置...")
        default_config = {
            "bot_token": "YOUR_BOT_TOKEN_HERE",
            "admins": [],
            "notify_users": [],
            "keywords": [],
            "keyword_rules": [],
            "settings": {
                "case_sensitive": False,
                "regex_enabled": False,
                "include_source_info": True,
                "alert_cooldown": 0,
                "max_message_length": 500
            },
            "whitelist_chats": [],
            "blacklist_chats": []
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"✅ 已创建配置文件，请编辑后重新运行")
        exit(1)

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            TOKEN = config_data.get("bot_token")
    except Exception as e:
        print(f"❌ 加载配置文件失败: {e}")
        exit(1)

    invalid_tokens = ["YOUR_BOT_TOKEN_HERE", "your_bot_token", ""]
    if not TOKEN or TOKEN in invalid_tokens:
        print(f"❌ 请在配置文件中设置有效的 bot_token")
        exit(1)

    print(f"✅ Token 加载成功: {TOKEN[:20]}...")

    bot = KeywordMonitorBot(TOKEN)
    bot. run()