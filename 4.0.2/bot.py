# main.py - 4.0 机器人监听同步端 (优化版)
# 功能: Telegram 转发机器人，支持多频道转发、AI重写、伪原创等功能

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
from telegram import Update, Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.constants import ParseMode
import sqlite3
from pathlib import Path
import html
import re
from collections import defaultdict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from openai import AsyncOpenAI

# 版本信息
VERSION = "4.0. 1"
BANNER = f"""
╔══════════════════════════════════════════════════════════╗
║       Telegram 转发机器人 v{VERSION}                       ║
║       多频道转发 | AI重写 | 伪原创 | 关键词过滤              ║
╚══════════════════════════════════════════════════════════╝
"""

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def escape_markdown_v2(text: str) -> str:
    """转义 MarkdownV2 特殊字符"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


class MediaGroupHandler:
    """媒体组处理器"""

    def __init__(self):
        self.media_groups: Dict[str, List[Message]] = defaultdict(list)
        self.group_timers: Dict[str, asyncio.Task] = {}
        self.timeout_seconds = 3

    async def add_message(self, message: Message, forward_callback):
        """添加消息到媒体组"""
        if not message.media_group_id:
            await forward_callback([message])
            return

        group_id = message.media_group_id
        self.media_groups[group_id].append(message)

        if group_id in self.group_timers:
            self.group_timers[group_id].cancel()

        self.group_timers[group_id] = asyncio.create_task(
            self._process_group_after_timeout(group_id, forward_callback)
        )

    async def _process_group_after_timeout(self, group_id: str, forward_callback):
        """超时后处理媒体组"""
        await asyncio.sleep(self.timeout_seconds)

        if group_id in self.media_groups:
            messages = self.media_groups[group_id]
            messages.sort(key=lambda m: m.message_id)
            await forward_callback(messages)

            del self.media_groups[group_id]
            if group_id in self.group_timers:
                del self.group_timers[group_id]


class DeepSeekRewriter:
    """DeepSeek AI 文本重写器"""

    def __init__(self, config: dict):
        self.config = config
        self.client = None
        self._init_client()

    def _init_client(self):
        """初始化 OpenAI 客户端"""
        settings = self.config.get('deepseek_settings', {})
        api_key = settings.get('api_key', '')
        base_url = settings.get('base_url', 'https://api.deepseek.com')

        if api_key and api_key not in ['', 'put your api key here', 'your_api_key']:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
            logger.info("✅ DeepSeek 客户端已初始化")
        else:
            self.client = None
            logger.info("ℹ️ DeepSeek API Key 未配置")

    def update_config(self, config: dict):
        """更新配置"""
        self.config = config
        self._init_client()

    async def rewrite_text(self, text: str) -> str:
        """使用 DeepSeek 重写文本"""
        settings = self.config.get('deepseek_settings', {})

        if not settings.get('enabled', False):
            return text

        if not self.client:
            logger.warning("DeepSeek 客户端未初始化")
            return text

        if not text or not text.strip():
            return text

        try:
            system_prompt = settings.get('system_prompt',
                                         "你是一个专业的文本重写助手。请将用户提供的文本进行重写，保持原意但使用不同的表达方式。只返回重写后的文本，不要添加任何解释。")
            model = settings.get('model', 'deepseek-chat')
            max_tokens = settings.get('max_tokens', 2000)
            temperature = settings.get('temperature', 0.7)

            logger.info(f"开始 DeepSeek 重写...")

            response = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )

            rewritten_text = response.choices[0].message.content.strip()
            logger.info(f"✅ DeepSeek 重写成功")
            return rewritten_text

        except Exception as e:
            logger.error(f"❌ DeepSeek 重写失败: {e}")
            return text


class TelegramForwardBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.db_path = "forward_bot.db"
        self.config_file = "bot_config.json"

        self.media_group_handler = MediaGroupHandler()
        self.init_database()
        self.config = self.load_config()
        self.deepseek_rewriter = DeepSeekRewriter(self.config)

        self.stats = {
            'messages_received': 0,
            'messages_forwarded': 0,
            'failed_forwards': 0,
            'media_groups_forwarded': 0,
            'start_time': datetime.now()
        }

        self.register_handlers()

    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_channels (
                id INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS target_channels (
                id INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS forward_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id INTEGER,
                target_chat_id INTEGER,
                original_message_id INTEGER,
                forwarded_message_id INTEGER,
                content_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                error_message TEXT
            )
        ''')

        cursor.execute("PRAGMA table_info(forward_logs)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'media_group_id' not in columns:
            cursor.execute('ALTER TABLE forward_logs ADD COLUMN media_group_id TEXT')

        if 'is_media_group' not in columns:
            cursor.execute('ALTER TABLE forward_logs ADD COLUMN is_media_group BOOLEAN DEFAULT FALSE')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def load_config(self) -> dict:
        """加载配置文件"""
        default_config = {
            "bot_token": "YOUR_BOT_TOKEN_HERE",
            "admins": [],
            "source_channels": [],
            "target_channels": [],
            "forward_settings": {
                "preserve_sender": True,
                "add_source_info": True,
                "filter_content_types": [],
                "keyword_filter": [],
                "delay_seconds": 0,
                "batch_forward": False,
                "max_forwards_per_minute": 60,
                "media_group_timeout": 3
            },
            "notification_settings": {
                "notify_admin_on_error": True,
                "daily_report": True,
                "report_channel": None
            },
            "paraphrase_rules": {},
            "deepseek_settings": {
                "enabled": False,
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "system_prompt": "你是一个专业的文本重写助手。请将用户提供的文本进行重写，保持原意但使用不同的表达方式。保持原文的语言。只返回重写后的文本，不要添加任何解释。",
                "max_tokens": 2000,
                "temperature": 0.7
            }
        }

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    for key, value in default_config.items():
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
        # 基础命令
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("getid", self.getid_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))

        # 管理命令
        self.application.add_handler(CommandHandler("admin", self.admin_panel))

        # 回调查询处理器
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # 消息转发处理器 (放在最后)
        self.application.add_handler(MessageHandler(
            filters.ALL & (~filters.COMMAND),
            self.handle_message
        ))

    async def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员"""
        return user_id in self.config.get("admins", [])

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """开始命令"""
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name

        welcome_text = f"""🤖 *欢迎使用 Telegram 转发机器人 v{VERSION}*

👤 *您的信息:*
• 用户名: {escape_markdown_v2(user_name)}
• 用户ID: `{user_id}`

📋 *主要功能:*
• 📢 自动转发指定频道/群组的消息
• 🖼️ 支持媒体组完整转发
• 🤖 支持 AI 智能重写
• 📝 支持伪原创替换
• 🔍 支持关键词过滤
• 📊 详细的转发统计

🔧 *快速开始:*
1\\. 使用 `/getid` 获取频道ID
2\\. 使用 `/admin` 进入管理面板配置

📖 输入 `/help` 查看所有命令"""

        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """帮助命令"""
        help_text = """📖 *命令列表*

🔧 *基础命令:*
• `/start` \\- 启动机器人并查看欢迎信息
• `/help` \\- 显示此帮助信息
• `/getid` \\- 获取用户/群组/频道ID
• `/status` \\- 查看机器人运行状态
• `/stats` \\- 查看详细统计信息

⚙️ *管理命令 \\(仅管理员\\):*
• `/admin` \\- 打开管理面板

💡 *使用提示:*
1\\. 将机器人添加到源频道和目标频道
2\\.  在管理面板中配置源频道和目标频道
3\\. 机器人会自动转发消息

🔗 *获取频道ID方法:*
将消息转发到机器人，使用 `/getid` 命令"""

        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def getid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取ID命令 - 核心功能"""
        message = update.message
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        response_text = f"""🆔 *ID 信息*

👤 *您的用户ID:* `{user_id}`
💬 *当前聊天ID:* `{chat_id}`
📝 *聊天类型:* {chat_type}"""

        # 如果是回复消息，获取被回复消息的信息
        if message.reply_to_message:
            replied_msg = message.reply_to_message

            # 如果是转发的消息
            if replied_msg.forward_from_chat:
                forward_chat = replied_msg.forward_from_chat
                response_text += f"""

📤 *转发来源:*
• 频道/群组ID: `{forward_chat.id}`
• 名称: {escape_markdown_v2(forward_chat.title or '未知')}
• 类型: {forward_chat.type}"""

            elif replied_msg.forward_from:
                forward_user = replied_msg.forward_from
                response_text += f"""

📤 *转发来源:*
• 用户ID: `{forward_user.id}`
• 用户名: {escape_markdown_v2(forward_user.full_name)}"""

            # 被回复消息的发送者
            if replied_msg.from_user:
                response_text += f"""

📨 *被回复消息发送者:*
• 用户ID: `{replied_msg.from_user.id}`
• 用户名: {escape_markdown_v2(replied_msg.from_user.full_name)}"""

        response_text += """

💡 *提示:* 回复一条转发的消息并使用此命令，可以获取原始频道的ID"""

        await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """状态命令"""
        user_id = update.effective_user.id

        uptime = datetime.now() - self.stats['start_time']
        uptime_str = str(uptime).split('.')[0]

        deepseek_status = "✅ 已开启" if self.config.get('deepseek_settings', {}).get('enabled', False) else "❌ 已关闭"

        is_admin = await self.is_admin(user_id)
        admin_status = "✅ 是" if is_admin else "❌ 否"

        status_text = f"""📊 *机器人状态*

🕐 *运行时间:* {escape_markdown_v2(uptime_str)}
📥 *接收消息:* {self.stats['messages_received']}
📤 *转发成功:* {self.stats['messages_forwarded']}
🖼️ *媒体组转发:* {self.stats['media_groups_forwarded']}
❌ *转发失败:* {self.stats['failed_forwards']}

📢 *源频道数量:* {len(self.config['source_channels'])}
🎯 *目标频道数量:* {len(self.config['target_channels'])}
👥 *管理员数量:* {len(self.config['admins'])}

⚙️ *当前设置:*
• 转发延迟: {self.config['forward_settings']['delay_seconds']}秒
• 显示来源: {'✅' if self.config['forward_settings']['add_source_info'] else '❌'}
• AI重写: {deepseek_status}

👤 *您的管理员状态:* {admin_status}"""

        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """统计命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            await update.message.reply_text("❌ 您没有权限查看统计信息")
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT COUNT(*), content_type
            FROM forward_logs 
            WHERE DATE(timestamp) = DATE('now')
            GROUP BY content_type
        ''')
        today_stats = cursor.fetchall()

        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success
            FROM forward_logs 
            WHERE DATE(timestamp) = DATE('now')
        ''')
        success_stats = cursor.fetchone()

        conn.close()

        stats_text = "📈 *详细统计*\n\n"

        if today_stats:
            stats_text += "📅 *今日转发统计:*\n"
            for count, content_type in today_stats:
                content_type_safe = escape_markdown_v2(content_type or '未知')
                stats_text += f"• {content_type_safe}: {count}条\n"

        if success_stats and success_stats[0] > 0:
            success_rate = (success_stats[1] / success_stats[0]) * 100
            stats_text += f"\n✅ *今日成功率:* {success_rate:.1f}%"
        else:
            stats_text += "\n📭 今日暂无转发记录"

        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理面板"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            await update.message.reply_text(
                f"❌ 您没有权限使用此机器人\n\n您的用户ID: `{user_id}`\n请联系管理员添加您为管理员",
                parse_mode=ParseMode.MARKDOWN_V2)
            return
        await self.send_admin_panel(update.effective_chat.id, context)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """按钮回调处理"""
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id

        if not await self.is_admin(user_id):
            await query.edit_message_text(text="❌ 您没有权限使用此机器人")
            return

        data = query.data

        # 菜单导航
        menu_handlers = {
            "main_menu": (self.send_admin_panel, 'main_menu'),
            "admin_management_menu": (self.send_admin_management_panel, 'admin_management'),
            "forward_settings_menu": (self.send_forward_settings_panel, 'forward_settings'),
            "paraphrase_settings_menu": (self.send_paraphrase_settings_panel, 'paraphrase_settings'),
            "keyword_filter_menu": (self.send_keyword_filter_panel, 'keyword_filter'),
            "deepseek_settings_menu": (self.send_deepseek_settings_panel, 'deepseek_settings'),
        }

        if data in menu_handlers:
            handler, menu_name = menu_handlers[data]
            await handler(query.message.chat_id, context)
            context.user_data['last_menu'] = menu_name
            return

        # 输入提示处理
        input_prompts = {
            "add_admin_prompt": ("请发送要添加的管理员用户ID\n\n💡 提示: 用户可以使用 /getid 命令获取自己的ID",
                                 'add_admin'),
            "remove_admin_prompt": ("请发送要移除的管理员用户ID", 'remove_admin'),
            "add_source_prompt": (
                "请发送要添加的源频道ID\n\n💡 提示: 转发频道消息到机器人后使用 /getid 获取频道ID\n格式示例: `-1001234567890`",
                'add_source'),
            "remove_source_prompt": ("请发送要移除的源频道ID", 'remove_source'),
            "add_target_prompt": (
                "请发送要添加的目标频道ID\n\n💡 提示: 转发频道消息到机器人后使用 /getid 获取频道ID\n格式示例: `-1001234567890`",
                'add_target'),
            "remove_target_prompt": ("请发送要移除的目标频道ID", 'remove_target'),
            "set_delay_prompt": ("请发送转发延迟秒数 (例如: 5)", 'set_delay'),
            "add_paraphrase_rule_prompt": ("请发送伪原创规则，格式: `原词=替换词`\n\n例如: `免费=限免`",
                                           'add_paraphrase_rule'),
            "remove_paraphrase_rule_prompt": ("请发送要删除的伪原创规则的原词", 'remove_paraphrase_rule'),
            "add_keyword_filter_prompt": ("请发送要添加的过滤关键词\n\n包含此关键词的消息将不会被转发",
                                          'add_keyword_filter'),
            "remove_keyword_filter_prompt": ("请发送要删除的过滤关键词", 'remove_keyword_filter'),
            "set_deepseek_api_key_prompt": ("请发送 DeepSeek API Key:", 'set_deepseek_api_key'),
            "set_deepseek_prompt_prompt": (
                f"请发送新的系统提示词 (System Prompt)\n\n当前提示词:\n{self.config.get('deepseek_settings', {}).get('system_prompt', '未设置')[:300]}...",
                'set_deepseek_prompt'),
            "set_deepseek_model_prompt": (
                f"请发送模型名称\n\n当前模型: {self.config.get('deepseek_settings', {}).get('model', 'deepseek-chat')}\n常用模型: deepseek-chat, deepseek-reasoner",
                'set_deepseek_model'),
            "set_deepseek_temperature_prompt": (
                f"请发送温度值 (0.0-2.0)\n\n当前温度: {self.config.get('deepseek_settings', {}).get('temperature', 0.7)}\n数值越高创造性越强",
                'set_deepseek_temperature'),
            "set_deepseek_baseurl_prompt": (
                f"请发送 API Base URL\n\n当前地址: {self.config.get('deepseek_settings', {}).get('base_url', 'https://api.deepseek.com')}",
                'set_deepseek_baseurl'),
            "test_deepseek": ("请发送要测试重写的文本:", 'test_deepseek'),
        }

        if data in input_prompts:
            prompt_text, action = input_prompts[data]
            await query.edit_message_text(text=prompt_text)
            context.user_data['awaiting_input'] = action
            return

        # 列表显示处理
        if data == "list_admins":
            admins = self.config.get('admins', [])
            if not admins:
                text = "👥 当前没有配置管理员"
            else:
                text = "👥 *管理员列表:*\n\n"
                for i, admin_id in enumerate(admins, 1):
                    text += f"{i}\\. `{admin_id}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_sources":
            sources = self.config.get('source_channels', [])
            if not sources:
                text = "📢 当前没有配置源频道"
            else:
                text = "📢 *源频道列表:*\n\n"
                for i, source_id in enumerate(sources, 1):
                    text += f"{i}\\. `{source_id}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_targets":
            targets = self.config.get('target_channels', [])
            if not targets:
                text = "🎯 当前没有配置目标频道"
            else:
                text = "🎯 *目标频道列表:*\n\n"
                for i, target_id in enumerate(targets, 1):
                    text += f"{i}\\. `{target_id}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_paraphrase_rules":
            rules = self.config.get('paraphrase_rules', {})
            if not rules:
                text = "📝 当前没有配置伪原创规则"
            else:
                text = "📝 *伪原创规则列表:*\n\n"
                for i, (key, value) in enumerate(rules.items(), 1):
                    text += f"{i}\\.  `{escape_markdown_v2(key)}` → `{escape_markdown_v2(value)}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if data == "list_keyword_filters":
            keywords = self.config['forward_settings']['keyword_filter']
            if not keywords:
                text = "🔍 当前没有配置过滤关键词"
            else:
                text = "🔍 *过滤关键词列表:*\n\n"
                for i, keyword in enumerate(keywords, 1):
                    text += f"{i}\\. `{escape_markdown_v2(keyword)}`\n"
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        # 切换开关处理
        if data == "toggle_source_info":
            current = self.config['forward_settings']['add_source_info']
            self.config['forward_settings']['add_source_info'] = not current
            self.save_config()
            status = "开启" if not current else "关闭"
            await query.edit_message_text(text=f"✅ 来源信息显示已{status}")

        if data == "toggle_deepseek":
            if 'deepseek_settings' not in self.config:
                self.config['deepseek_settings'] = {}
            current = self.config['deepseek_settings'].get('enabled', False)
            self.config['deepseek_settings']['enabled'] = not current
            self.save_config()
            self.deepseek_rewriter.update_config(self.config)
            status = "开启" if not current else "关闭"
            await query.edit_message_text(text=f"✅ DeepSeek AI 重写已{status}")

        if data == "show_deepseek_status":
            settings = self.config.get('deepseek_settings', {})
            enabled = "✅ 已开启" if settings.get('enabled') else "❌ 已关闭"
            api_configured = "✅ 已配置" if settings.get('api_key') and settings.get('api_key') not in ['',
                                                                                                       'put your api key here'] else "❌ 未配置"
            model = settings.get('model', 'deepseek-chat')
            temperature = settings.get('temperature', 0.7)
            base_url = settings.get('base_url', 'https://api. deepseek.com')
            prompt_preview = settings.get('system_prompt', '未设置')[:200]

            status_text = f"""🤖 DeepSeek AI 重写状态

状态: {enabled}
API Key: {api_configured}
API 地址: {base_url}
模型: {model}
温度: {temperature}

系统提示词:
{prompt_preview}.. ."""
            await query.edit_message_text(text=status_text)
            return

        # 刷新面板
        await self._refresh_panel(query.message.chat_id, context)

    async def _refresh_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """刷新当前面板"""
        last_menu = context.user_data.get('last_menu', 'main_menu')
        menu_handlers = {
            "admin_management": self.send_admin_management_panel,
            "forward_settings": self.send_forward_settings_panel,
            "paraphrase_settings": self.send_paraphrase_settings_panel,
            "keyword_filter": self.send_keyword_filter_panel,
            "deepseek_settings": self.send_deepseek_settings_panel,
        }
        handler = menu_handlers.get(last_menu, self.send_admin_panel)
        await handler(chat_id, context)

    async def send_admin_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送主管理面板"""
        keyboard = [
            [InlineKeyboardButton("👥 管理管理员", callback_data="admin_management_menu")],
            [InlineKeyboardButton("➡️ 转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text=f"⚙️ 主管理面板 v{VERSION}", reply_markup=reply_markup)

    async def send_admin_management_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送管理员管理子菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加管理员", callback_data="add_admin_prompt")],
            [InlineKeyboardButton("➖ 移除管理员", callback_data="remove_admin_prompt")],
            [InlineKeyboardButton("📋 列出管理员", callback_data="list_admins")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="👥 管理员管理", reply_markup=reply_markup)

    async def send_forward_settings_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送转发设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加源频道", callback_data="add_source_prompt"),
             InlineKeyboardButton("➖ 移除源频道", callback_data="remove_source_prompt")],
            [InlineKeyboardButton("📋 列出源频道", callback_data="list_sources")],
            [InlineKeyboardButton("➕ 添加目标频道", callback_data="add_target_prompt"),
             InlineKeyboardButton("➖ 移除目标频道", callback_data="remove_target_prompt")],
            [InlineKeyboardButton("📋 列出目标频道", callback_data="list_targets")],
            [InlineKeyboardButton("⏱️ 设置转发延迟", callback_data="set_delay_prompt")],
            [InlineKeyboardButton("🔄 切换来源信息显示", callback_data="toggle_source_info")],
            [InlineKeyboardButton("📝 伪原创设置", callback_data="paraphrase_settings_menu")],
            [InlineKeyboardButton("🔍 关键词过滤", callback_data="keyword_filter_menu")],
            [InlineKeyboardButton("🤖 AI重写设置", callback_data="deepseek_settings_menu")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="➡️ 转发设置", reply_markup=reply_markup)

    async def send_paraphrase_settings_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送伪原创设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加规则", callback_data="add_paraphrase_rule_prompt")],
            [InlineKeyboardButton("📋 列出规则", callback_data="list_paraphrase_rules")],
            [InlineKeyboardButton("➖ 删除规则", callback_data="remove_paraphrase_rule_prompt")],
            [InlineKeyboardButton("🔙 返回转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id,
                                       text="📝 伪原创设置\n\n伪原创规则会将消息中的特定词汇替换为其他词汇",
                                       reply_markup=reply_markup)

    async def send_keyword_filter_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送关键词过滤设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("➕ 添加关键词", callback_data="add_keyword_filter_prompt")],
            [InlineKeyboardButton("📋 列出关键词", callback_data="list_keyword_filters")],
            [InlineKeyboardButton("➖ 删除关键词", callback_data="remove_keyword_filter_prompt")],
            [InlineKeyboardButton("🔙 返回转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="🔍 关键词过滤设置\n\n包含过滤关键词的消息将不会被转发",
                                       reply_markup=reply_markup)

    async def send_deepseek_settings_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送 DeepSeek AI 重写设置子菜单"""
        settings = self.config.get('deepseek_settings', {})
        enabled_text = "🟢 已开启" if settings.get('enabled') else "🔴 已关闭"

        keyboard = [
            [InlineKeyboardButton(f"🔄 切换 AI 重写 ({enabled_text})", callback_data="toggle_deepseek")],
            [InlineKeyboardButton("🔑 设置 API Key", callback_data="set_deepseek_api_key_prompt")],
            [InlineKeyboardButton("🌐 设置 API 地址", callback_data="set_deepseek_baseurl_prompt")],
            [InlineKeyboardButton("📝 设置系统提示词", callback_data="set_deepseek_prompt_prompt")],
            [InlineKeyboardButton("🤖 设置模型", callback_data="set_deepseek_model_prompt")],
            [InlineKeyboardButton("🌡️ 设置温度", callback_data="set_deepseek_temperature_prompt")],
            [InlineKeyboardButton("📊 查看当前状态", callback_data="show_deepseek_status")],
            [InlineKeyboardButton("🧪 测试重写", callback_data="test_deepseek")],
            [InlineKeyboardButton("🔙 返回转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="🤖 DeepSeek AI 重写设置", reply_markup=reply_markup)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息"""
        message = update.message
        if not message:
            return

        chat_id = message.chat_id
        user_id = message.from_user.id if message.from_user else None

        # 如果用户正在等待输入（管理员操作）
        if user_id and user_id in self.config.get("admins", []) and context.user_data.get('awaiting_input'):
            await self.handle_admin_input(update, context)
            return

        # 检查是否来自源频道
        if chat_id not in self.config['source_channels']:
            return

        logger.info(f"收到来自源频道的消息: {chat_id}")
        self.stats['messages_received'] += 1

        content_type = self.get_message_type(message)
        if self.should_filter_message(message, content_type):
            logger.info(f"消息 {message.message_id} 被过滤")
            return

        # 将消息传递给媒体组处理器
        await self.media_group_handler.add_message(message, self.forward_messages_group)

    async def handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理管理员输入"""
        chat_id = update.effective_chat.id
        input_text = update.message.text
        action = context.user_data.pop('awaiting_input', None)

        if not action:
            return

        try:
            if action == 'add_admin':
                new_admin_id = int(input_text)
                if new_admin_id not in self.config['admins']:
                    self.config['admins'].append(new_admin_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加管理员: `{new_admin_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户已是管理员")

            elif action == 'remove_admin':
                admin_id = int(input_text)
                if admin_id in self.config['admins']:
                    self.config['admins'].remove(admin_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除管理员: `{admin_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户不是管理员")

            elif action == 'add_source':
                channel_id = int(input_text)
                if channel_id not in self.config['source_channels']:
                    self.config['source_channels'].append(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加源频道: `{channel_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道已存在于源列表中")

            elif action == 'remove_source':
                channel_id = int(input_text)
                if channel_id in self.config['source_channels']:
                    self.config['source_channels'].remove(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除源频道: `{channel_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道不在源列表中")

            elif action == 'add_target':
                channel_id = int(input_text)
                if channel_id not in self.config['target_channels']:
                    self.config['target_channels'].append(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加目标频道: `{channel_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道已存在于目标列表中")

            elif action == 'remove_target':
                channel_id = int(input_text)
                if channel_id in self.config['target_channels']:
                    self.config['target_channels'].remove(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除目标频道: `{channel_id}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道不在目标列表中")

            elif action == 'set_delay':
                delay = int(input_text)
                if delay < 0:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 延迟时间不能为负数")
                else:
                    self.config['forward_settings']['delay_seconds'] = delay
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 转发延迟已设置为 {delay} 秒")

            elif action == 'add_paraphrase_rule':
                if '=' in input_text:
                    key, value = input_text.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        self.config['paraphrase_rules'][key] = value
                        self.save_config()
                        await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加伪原创规则: `{key}` → `{value}`",
                                                       parse_mode=ParseMode.MARKDOWN_V2)
                    else:
                        await context.bot.send_message(chat_id=chat_id, text="❌ 规则格式不正确")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 规则格式不正确，请使用 `原词=替换词` 格式")

            elif action == 'remove_paraphrase_rule':
                key = input_text.strip()
                if key in self.config['paraphrase_rules']:
                    del self.config['paraphrase_rules'][key]
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已删除伪原创规则: `{key}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该规则不存在")

            elif action == 'add_keyword_filter':
                keyword = input_text.strip()
                if keyword and keyword not in self.config['forward_settings']['keyword_filter']:
                    self.config['forward_settings']['keyword_filter'].append(keyword)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加过滤关键词: `{keyword}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 关键词已存在或无效")

            elif action == 'remove_keyword_filter':
                keyword = input_text.strip()
                if keyword in self.config['forward_settings']['keyword_filter']:
                    self.config['forward_settings']['keyword_filter'].remove(keyword)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已删除过滤关键词: `{keyword}`",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该关键词不存在")

            elif action == 'set_deepseek_api_key':
                api_key = input_text.strip()
                self.config['deepseek_settings']['api_key'] = api_key
                self.save_config()
                self.deepseek_rewriter.update_config(self.config)
                await context.bot.send_message(chat_id=chat_id, text="✅ DeepSeek API Key 已设置")

            elif action == 'set_deepseek_baseurl':
                base_url = input_text.strip()
                self.config['deepseek_settings']['base_url'] = base_url
                self.save_config()
                self.deepseek_rewriter.update_config(self.config)
                await context.bot.send_message(chat_id=chat_id, text=f"✅ DeepSeek API 地址已设置为: {base_url}")

            elif action == 'set_deepseek_prompt':
                prompt = input_text.strip()
                self.config['deepseek_settings']['system_prompt'] = prompt
                self.save_config()
                await context.bot.send_message(chat_id=chat_id, text="✅ DeepSeek 系统提示词已设置")

            elif action == 'set_deepseek_model':
                model = input_text.strip()
                self.config['deepseek_settings']['model'] = model
                self.save_config()
                await context.bot.send_message(chat_id=chat_id, text=f"✅ DeepSeek 模型已设置为: {model}")

            elif action == 'set_deepseek_temperature':
                temperature = float(input_text.strip())
                if 0.0 <= temperature <= 2.0:
                    self.config['deepseek_settings']['temperature'] = temperature
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ DeepSeek 温度已设置为: {temperature}")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 温度值必须在 0.0 到 2.0 之间")

            elif action == 'test_deepseek':
                await context.bot.send_message(chat_id=chat_id, text="⏳ 正在测试 DeepSeek 重写...")
                try:
                    original_enabled = self.config.get('deepseek_settings', {}).get('enabled', False)
                    self.config['deepseek_settings']['enabled'] = True
                    self.deepseek_rewriter.update_config(self.config)

                    rewritten = await self.deepseek_rewriter.rewrite_text(input_text)

                    self.config['deepseek_settings']['enabled'] = original_enabled
                    self.deepseek_rewriter.update_config(self.config)

                    result_text = f"""🧪 DeepSeek 重写测试结果

📝 原文:
{input_text}

✨ 重写后:
{rewritten}"""
                    await context.bot.send_message(chat_id=chat_id, text=result_text)
                except Exception as e:
                    await context.bot.send_message(chat_id=chat_id, text=f"❌ 测试失败: {e}")

        except ValueError:
            await context.bot.send_message(chat_id=chat_id, text="❌ 无效的输入，请输入正确的数字")
        except Exception as e:
            logger.error(f"处理管理员输入失败: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"❌ 处理请求时发生错误: {e}")
        finally:
            await self._refresh_panel(chat_id, context)

    def get_message_type(self, message: Message) -> str:
        """获取消息类型"""
        if message.text:
            return "text"
        elif message.photo:
            return "photo"
        elif message.video:
            return "video"
        elif message.document:
            return "document"
        elif message.audio:
            return "audio"
        elif message.voice:
            return "voice"
        elif message.sticker:
            return "sticker"
        elif message.animation:
            return "animation"
        elif message.location:
            return "location"
        elif message.poll:
            return "poll"
        else:
            return "other"

    def should_filter_message(self, message: Message, content_type: str) -> bool:
        """检查消息是否应被过滤"""
        # 内容类型过滤
        if content_type in self.config['forward_settings']['filter_content_types']:
            return True

        # 关键词过滤
        message_content = message.text or message.caption or ""
        if message_content and self.config['forward_settings']['keyword_filter']:
            text_lower = message_content.lower()
            for keyword in self.config['forward_settings']['keyword_filter']:
                if keyword.lower() in text_lower:
                    logger.info(f"消息被关键词过滤: {keyword}")
                    return True

        return False

    def apply_paraphrase_rules(self, text: str) -> str:
        """应用伪原创替换规则"""
        rules = self.config.get('paraphrase_rules', {})
        if not rules or not text:
            return text

        modified_text = text
        for old_word, new_word in rules.items():
            modified_text = modified_text.replace(old_word, new_word)
        return modified_text

    async def build_caption(self, message: Message) -> str:
        """构建转发消息的说明"""
        original_text = message.caption or message.text or ""

        # 1. 应用伪原创替换规则
        processed_text = self.apply_paraphrase_rules(original_text)

        # 2. 使用 DeepSeek AI 重写（如果启用）
        if self.config.get('deepseek_settings', {}).get('enabled', False):
            processed_text = await self.deepseek_rewriter.rewrite_text(processed_text)

        # 3. 添加来源信息（如果启用）
        if self.config['forward_settings']['add_source_info']:
            chat_title = message.chat.title or str(message.chat.id)
            time_str = message.date.strftime('%Y-%m-%d %H:%M:%S')
            source_info = f"\n\n📢 来源: {chat_title}\n⏰ 时间: {time_str}"

            if message.from_user and self.config['forward_settings']['preserve_sender']:
                sender_name = message.from_user.full_name
                source_info += f"\n👤 发送者: {sender_name}"

            processed_text += source_info

        return processed_text

    async def forward_messages_group(self, messages: List[Message]):
        """转发消息组"""
        if not messages:
            return

        targets = self.config['target_channels']
        if not targets:
            return

        # 转发延迟
        delay = self.config['forward_settings']['delay_seconds']
        if delay > 0:
            await asyncio.sleep(delay)

        is_media_group = len(messages) > 1 and messages[0].media_group_id

        if is_media_group:
            await self.forward_media_group(messages)
        else:
            await self.forward_single_message(messages[0])

    async def forward_media_group(self, messages: List[Message]):
        """转发媒体组"""
        targets = self.config['target_channels']

        for target_id in targets:
            try:
                media_list = []
                caption_text = await self.build_caption(messages[0])

                for i, message in enumerate(messages):
                    if i == 0:
                        input_media = self.create_input_media(message, caption_text)
                    else:
                        input_media = self.create_input_media(message)

                    if input_media:
                        media_list.append(input_media)

                if media_list:
                    await self.application.bot.send_media_group(
                        chat_id=target_id,
                        media=media_list
                    )

                    self.stats['messages_forwarded'] += len(messages)
                    self.stats['media_groups_forwarded'] += 1
                    logger.info(f"媒体组已转发: -> {target_id} ({len(messages)}条)")

                    # 记录日志
                    for msg in messages:
                        self.log_forward(msg.chat_id, target_id, msg.message_id, None,
                                         self.get_message_type(msg), msg.media_group_id, True, True, None)

            except Exception as e:
                error_msg = str(e)
                logger.error(f"媒体组转发失败 -> {target_id}: {error_msg}")
                self.stats['failed_forwards'] += len(messages)

                for msg in messages:
                    self.log_forward(msg.chat_id, target_id, msg.message_id, None,
                                     self.get_message_type(msg), msg.media_group_id, True, False, error_msg)

                if self.config['notification_settings']['notify_admin_on_error']:
                    await self.notify_admins_error(messages[0], target_id, error_msg)

    async def forward_single_message(self, message: Message):
        """转发单条消息"""
        targets = self.config['target_channels']
        content_type = self.get_message_type(message)

        for target_id in targets:
            try:
                need_process = (
                        self.config.get('deepseek_settings', {}).get('enabled', False) or
                        bool(self.config.get('paraphrase_rules', {})) or
                        self.config['forward_settings']['add_source_info']
                )

                if need_process:
                    caption = await self.build_caption(message)

                    if content_type == "text":
                        await self.application.bot.send_message(chat_id=target_id, text=caption)
                    elif content_type == "photo":
                        photo = message.photo[-1]
                        await self.application.bot.send_photo(chat_id=target_id, photo=photo.file_id, caption=caption)
                    elif content_type == "video":
                        await self.application.bot.send_video(chat_id=target_id, video=message.video.file_id,
                                                              caption=caption)
                    elif content_type == "document":
                        await self.application.bot.send_document(chat_id=target_id, document=message.document.file_id,
                                                                 caption=caption)
                    elif content_type == "audio":
                        await self.application.bot.send_audio(chat_id=target_id, audio=message.audio.file_id,
                                                              caption=caption)
                    elif content_type == "voice":
                        await self.application.bot.send_voice(chat_id=target_id, voice=message.voice.file_id,
                                                              caption=caption)
                    elif content_type == "animation":
                        await self.application.bot.send_animation(chat_id=target_id,
                                                                  animation=message.animation.file_id, caption=caption)
                    else:
                        await self.application.bot.copy_message(
                            chat_id=target_id,
                            from_chat_id=message.chat_id,
                            message_id=message.message_id
                        )
                else:
                    await self.application.bot.copy_message(
                        chat_id=target_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id
                    )

                self.stats['messages_forwarded'] += 1
                logger.info(f"消息已转发: -> {target_id}")
                self.log_forward(message.chat_id, target_id, message.message_id, None,
                                 content_type, None, False, True, None)

            except Exception as e:
                error_msg = str(e)
                logger.error(f"转发失败 -> {target_id}: {error_msg}")
                self.stats['failed_forwards'] += 1
                self.log_forward(message.chat_id, target_id, message.message_id, None,
                                 content_type, None, False, False, error_msg)

                if self.config['notification_settings']['notify_admin_on_error']:
                    await self.notify_admins_error(message, target_id, error_msg)

    def create_input_media(self, message: Message, caption: str = None):
        """创建 InputMedia 对象"""
        try:
            if message.photo:
                photo = message.photo[-1]
                return InputMediaPhoto(media=photo.file_id, caption=caption)
            elif message.video:
                return InputMediaVideo(media=message.video.file_id, caption=caption)
            elif message.document:
                return InputMediaDocument(media=message.document.file_id, caption=caption)
            elif message.audio:
                return InputMediaAudio(media=message.audio.file_id, caption=caption)
            else:
                return None
        except Exception as e:
            logger.error(f"创建 InputMedia 失败: {e}")
            return None

    def log_forward(self, source_chat_id: int, target_chat_id: int,
                    original_msg_id: int, forwarded_msg_id: int,
                    content_type: str, media_group_id: str, is_media_group: bool,
                    success: bool, error_msg: str):
        """记录转发日志"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO forward_logs 
                (source_chat_id, target_chat_id, original_message_id, 
                 forwarded_message_id, content_type, media_group_id, is_media_group, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (source_chat_id, target_chat_id, original_msg_id,
                  forwarded_msg_id, content_type, media_group_id, is_media_group, success, error_msg))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录转发日志失败: {e}")

    async def notify_admins_error(self, message: Message, target_id: int, error_msg: str):
        """通知管理员转发错误"""
        chat_title = message.chat.title or str(message.chat.id)
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        error_text = f"""❌ 转发失败通知

📢 源频道: {chat_title}
🎯 目标频道: {target_id}
⚠️ 错误信息: {error_msg}
⏰ 时间: {time_str}"""

        for admin_id in self.config['admins']:
            try:
                await self.application.bot.send_message(chat_id=admin_id, text=error_text)
            except Exception as e:
                logger.error(f"通知管理员失败 {admin_id}: {e}")

    def run(self):
        """运行机器人"""
        print(BANNER)
        logger.info("机器人启动中...")
        self.media_group_handler.timeout_seconds = self.config['forward_settings']['media_group_timeout']
        self.application.run_polling()


if __name__ == "__main__":
    # 从配置文件获取 token
    config_file = "bot_config.json"
    TOKEN = None

    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                TOKEN = config_data.get("bot_token")
        except Exception as e:
            print(f"❌ 加载配置文件 {config_file} 失败: {e}")
            exit(1)

    if not TOKEN or TOKEN in ["YOUR_BOT_TOKEN_HERE", "your bot_token", "put your token here"]:
        print("❌ 请在 bot_config.json 中设置有效的 bot_token")
        print("💡 示例: \"bot_token\": \"123456789:ABCdefGHIjklMNOpqrsTUVwxyz\"")
        exit(1)

    # 创建并运行机器人
    bot = TelegramForwardBot(TOKEN)
    bot.run()