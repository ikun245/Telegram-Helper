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
        self.timeout_seconds = 3  # 等待媒体组完成的超时时间
    
    async def add_message(self, message: Message, forward_callback):
        """添加消息到媒体组"""
        if not message.media_group_id:
            # 不是媒体组消息，直接转发
            await forward_callback([message])
            return
        
        group_id = message.media_group_id
        self.media_groups[group_id].append(message)
        
        # 如果已经有定时器，取消它
        if group_id in self.group_timers:
            self.group_timers[group_id].cancel()
        
        # 设置新的定时器
        self.group_timers[group_id] = asyncio.create_task(
            self._process_group_after_timeout(group_id, forward_callback)
        )
    
    async def _process_group_after_timeout(self, group_id: str, forward_callback):
        """超时后处理媒体组"""
        await asyncio.sleep(self.timeout_seconds)
        
        if group_id in self.media_groups:
            messages = self.media_groups[group_id]
            # 按消息ID排序确保顺序正确
            messages.sort(key=lambda m: m.message_id)
            await forward_callback(messages)
            
            # 清理
            del self.media_groups[group_id]
            if group_id in self.group_timers:
                del self.group_timers[group_id]

class TelegramForwardBot:
    def __init__(self, token: str):
        self.token = token
        # job_queue_instance = Application.get_default_job_queue() # 获取默认的 JobQueue 实例
        self.application = Application.builder().token(token).build()
        self.db_path = "forward_bot.db"
        self.config_file = "bot_config.json"
        
        # 媒体组处理器
        self.media_group_handler = MediaGroupHandler()
        
        # 初始化数据库
        self.init_database()
        
        # 加载配置
        self.config = self.load_config()
        
        # 统计数据
        self.stats = {
            'messages_received': 0,
            'messages_forwarded': 0,
            'failed_forwards': 0,
            'media_groups_forwarded': 0,
            'start_time': datetime.now()
        }
        
        # 注册处理器
        self.register_handlers()
    
    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建源频道/群组表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_channels (
                id INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # 创建目标频道/群组表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS target_channels (
                id INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # 创建转发日志表
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
        
        # 检查并添加新列
        cursor.execute("PRAGMA table_info(forward_logs)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'media_group_id' not in columns:
            cursor.execute('ALTER TABLE forward_logs ADD COLUMN media_group_id TEXT')
            logger.info("添加了 media_group_id 列")
        
        if 'is_media_group' not in columns:
            cursor.execute('ALTER TABLE forward_logs ADD COLUMN is_media_group BOOLEAN DEFAULT FALSE')
            logger.info("添加了 is_media_group 列")
        
        # 创建管理员表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建已采集数据表 - 移除
        # cursor.execute('''
        #     CREATE TABLE IF NOT EXISTS collected_items (
        #         id INTEGER PRIMARY KEY AUTOINCREMENT,
        #         vod_id TEXT UNIQUE,
        #         vod_name TEXT,
        #         vod_remarks TEXT,
        #         vod_play_from TEXT,
        #         detail_url TEXT,
        #         collect_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        #         sent_to_telegram BOOLEAN DEFAULT FALSE
        #     )
        # ''')
        
        conn.commit()
        conn.close()
    
    def load_config(self) -> dict:
        """加载配置文件"""
        default_config = {
            "admins": [],  # 管理员用户ID列表
            "source_channels": [],  # 源频道/群组ID列表
            "target_channels": [],  # 目标频道/群组ID列表
            "forward_settings": {
                "preserve_sender": True,  # 保留发送者信息
                "add_source_info": True,  # 添加来源信息
                "filter_content_types": [],  # 过滤的内容类型
                "keyword_filter": [],  # 关键词过滤
                "delay_seconds": 0,  # 转发延迟(秒)
                "batch_forward": False,  # 批量转发模式
                "max_forwards_per_minute": 60,  # 每分钟最大转发数
                "media_group_timeout": 3  # 媒体组超时时间(秒)
            },
            "notification_settings": {
                "notify_admin_on_error": True,
                "daily_report": True,
                "report_channel": None
            },
            "paraphrase_rules": {}
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置
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
        # 管理员命令
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("admin", self.admin_panel))

        # 回调查询处理器
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        
        # 消息转发处理器
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
        # 移除管理员权限检查，所有用户都可以使用 /start
        # if not await self.is_admin(user_id):
        #     await update.message.reply_text("❌ 您没有权限使用此机器人")
        #     return
        
        welcome_text = """🤖 *Telegram 转发机器人已启动*

*主要功能:*
📢 自动转发指定频道/群组的消息到多个目标
📊 详细的转发统计和日志
⚙️ 灵活的配置选项
🔒 管理员权限控制
🖼️ 支持媒体组完整转发

*快速开始:*
1\. 输入 `/admin` 进入管理面板进行配置。

输入 `/help` 查看所有命令"""
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """帮助命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        help_text = """📖 命令列表\n\n基础命令:\n• /start - 启动机器人\n• /help - 显示帮助\n• /admin - 管理面板"""
        await update.message.reply_text(help_text)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理面板"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            await update.message.reply_text("❌ 您没有权限使用此机器人")
            return
        await self.send_admin_panel(update.effective_chat.id, context)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id

        if not await self.is_admin(user_id):
            await query.edit_message_text(text="❌ 您没有权限使用此机器人")
            return

        # 主菜单导航
        if query.data == "main_menu":
            await self.send_admin_panel(query.message.chat_id, context)
            context.user_data['last_menu'] = 'main_menu' # 保存当前菜单状态
            return
        elif query.data == "admin_management_menu":
            await self.send_admin_management_panel(query.message.chat_id, context)
            context.user_data['last_menu'] = 'admin_management' # 保存当前菜单状态
            return
        elif query.data == "forward_settings_menu":
            await self.send_forward_settings_panel(query.message.chat_id, context)
            context.user_data['last_menu'] = 'forward_settings' # 保存当前菜单状态
            return
        elif query.data == "paraphrase_settings_menu":
            await self.send_paraphrase_settings_panel(query.message.chat_id, context)
            context.user_data['last_menu'] = 'paraphrase_settings' # 保存当前菜单状态
            return
        elif query.data == "keyword_filter_menu": # New callback for keyword filter menu
            await self.send_keyword_filter_panel(query.message.chat_id, context)
            context.user_data['last_menu'] = 'keyword_filter' # Save current menu state
            return

        # 管理员管理
        if query.data == "add_admin_prompt":
            await query.edit_message_text(text="请发送要添加的管理员用户ID (例如: `123456789`)")
            context.user_data['awaiting_input'] = 'add_admin'
        elif query.data == "list_admins":
            admins = self.config.get('admins', [])
            if not admins:
                await query.edit_message_text(text="👥 当前没有配置管理员")
                return
            text = "👥 管理员列表:\n\n"
            for i, admin_id in enumerate(admins, 1):
                text += f"{i}. `{admin_id}`\n"
            await query.edit_message_text(text=text)

        # 转发设置
        elif query.data == "add_source_prompt":
            await query.edit_message_text(text="请发送要添加的源频道ID (例如: `-1001234567890`)")
            context.user_data['awaiting_input'] = 'add_source'
        elif query.data == "remove_source_prompt":
            await query.edit_message_text(text="请发送要移除的源频道ID (例如: `-1001234567890`)")
            context.user_data['awaiting_input'] = 'remove_source'
        elif query.data == "list_sources":
            sources = self.config.get('source_channels', [])
            if not sources:
                await query.edit_message_text(text="📢 当前没有配置源频道")
                return
            text = "📢 源频道列表:\n\n"
            for i, source_id in enumerate(sources, 1):
                text += f"{i}. `{source_id}`\n"
            await query.edit_message_text(text=text)
        elif query.data == "add_target_prompt":
            await query.edit_message_text(text="请发送要添加的目标频道ID (例如: `-1001234567890`)")
            context.user_data['awaiting_input'] = 'add_target'
        elif query.data == "remove_target_prompt":
            await query.edit_message_text(text="请发送要移除的目标频道ID (例如: `-1001234567890`)")
            context.user_data['awaiting_input'] = 'remove_target'
        elif query.data == "list_targets":
            targets = self.config.get('target_channels', [])
            if not targets:
                await query.edit_message_text(text="🎯 当前没有配置目标频道")
                return
            text = "🎯 目标频道列表:\n\n"
            for i, target_id in enumerate(targets, 1):
                text += f"{i}. `{target_id}`\n"
            await query.edit_message_text(text=text)
        elif query.data == "set_delay_prompt":
            await query.edit_message_text(text="请发送转发延迟秒数 (例如: `5`)")
            context.user_data['awaiting_input'] = 'set_delay'
        elif query.data == "toggle_source_info":
            current = self.config['forward_settings']['add_source_info']
            self.config['forward_settings']['add_source_info'] = not current
            self.save_config()
            status = "开启" if not current else "关闭"
            await query.edit_message_text(text=f"✅ 来源信息显示已{status}")

        # 伪原创设置
        elif query.data == "add_paraphrase_rule_prompt":
            await query.edit_message_text(text="请发送伪原创规则，格式为 `A=B`")
            context.user_data['awaiting_input'] = 'add_paraphrase_rule'
        elif query.data == "list_paraphrase_rules":
            rules = self.config.get('paraphrase_rules', {})
            if not rules:
                await query.edit_message_text(text="❌ 当前没有配置伪原创规则")
                return
            text = "📝 伪原创规则列表:\n\n"
            for i, (key, value) in enumerate(rules.items(), 1):
                text += f"{i}. `{key}` = `{value}`\n"
            await query.edit_message_text(text=text)
        elif query.data == "remove_paraphrase_rule_prompt":
            await query.edit_message_text(text="请发送要删除的伪原创规则的键 (例如: `A`)")
            context.user_data['awaiting_input'] = 'remove_paraphrase_rule'

        # 关键词过滤设置
        elif query.data == "add_keyword_filter_prompt":
            await query.edit_message_text(text="请发送要添加的过滤关键词")
            context.user_data['awaiting_input'] = 'add_keyword_filter'
        elif query.data == "list_keyword_filters":
            keywords = self.config['forward_settings']['keyword_filter']
            if not keywords:
                await query.edit_message_text(text="❌ 当前没有配置过滤关键词")
                return
            text = "关键词过滤列表:\n\n"
            for i, keyword in enumerate(keywords, 1):
                text += f"{i}. `{keyword}`\n"
            await query.edit_message_text(text=text)
        elif query.data == "remove_keyword_filter_prompt":
            await query.edit_message_text(text="请发送要删除的过滤关键词")
            context.user_data['awaiting_input'] = 'remove_keyword_filter'

        # 最后刷新当前面板，以便用户可以看到更改（例如模式切换）
        if query.data not in ["main_menu", "admin_management_menu", "forward_settings_menu", "paraphrase_settings_menu", "keyword_filter_menu"]:
            # 如果不是菜单导航，则刷新当前菜单
            if context.user_data.get('last_menu') == "admin_management":
                await self.send_admin_management_panel(query.message.chat_id, context)
            elif context.user_data.get('last_menu') == "forward_settings":
                await self.send_forward_settings_panel(query.message.chat_id, context)
            elif context.user_data.get('last_menu') == "paraphrase_settings":
                await self.send_paraphrase_settings_panel(query.message.chat_id, context)
            elif context.user_data.get('last_menu') == "keyword_filter": # Refresh keyword filter menu
                await self.send_keyword_filter_panel(query.message.chat_id, context)
            else:
                await self.send_admin_panel(query.message.chat_id, context)

    async def send_admin_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送主管理面板"""
        keyboard = [
            [InlineKeyboardButton("管理管理员", callback_data="admin_management_menu")],
            [InlineKeyboardButton("转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="⚙️ 主管理面板", reply_markup=reply_markup)

    async def send_admin_management_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送管理员管理子菜单"""
        keyboard = [
            [InlineKeyboardButton("添加管理员", callback_data="add_admin_prompt")],
            [InlineKeyboardButton("列出管理员", callback_data="list_admins")],
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="👥 管理员管理", reply_markup=reply_markup)

    async def send_forward_settings_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送转发设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("添加源频道", callback_data="add_source_prompt")],
            [InlineKeyboardButton("移除源频道", callback_data="remove_source_prompt")],
            [InlineKeyboardButton("列出源频道", callback_data="list_sources")],
            [InlineKeyboardButton("添加目标频道", callback_data="add_target_prompt")],
            [InlineKeyboardButton("移除目标频道", callback_data="remove_target_prompt")],
            [InlineKeyboardButton("列出目标频道", callback_data="list_targets")],
            [InlineKeyboardButton("设置转发延迟", callback_data="set_delay_prompt")],
            [InlineKeyboardButton("切换来源信息显示", callback_data="toggle_source_info")],
            [InlineKeyboardButton("伪原创设置", callback_data="paraphrase_settings_menu")],
            [InlineKeyboardButton("关键词过滤", callback_data="keyword_filter_menu")], # New button
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="➡️ 转发设置", reply_markup=reply_markup)

    async def send_paraphrase_settings_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送伪原创设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("添加伪原创规则", callback_data="add_paraphrase_rule_prompt")],
            [InlineKeyboardButton("列出伪原创规则", callback_data="list_paraphrase_rules")],
            [InlineKeyboardButton("删除伪原创规则", callback_data="remove_paraphrase_rule_prompt")],
            [InlineKeyboardButton("🔙 返回转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="📝 伪原创设置", reply_markup=reply_markup)

    async def send_keyword_filter_panel(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """发送关键词过滤设置子菜单"""
        keyboard = [
            [InlineKeyboardButton("添加过滤关键词", callback_data="add_keyword_filter_prompt")],
            [InlineKeyboardButton("列出过滤关键词", callback_data="list_keyword_filters")],
            [InlineKeyboardButton("删除过滤关键词", callback_data="remove_keyword_filter_prompt")],
            [InlineKeyboardButton("🔙 返回转发设置", callback_data="forward_settings_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text="关键词过滤设置", reply_markup=reply_markup)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息转发"""
        message = update.message
        if not message:
            return
        
        chat_id = message.chat_id
        user_id = message.from_user.id if message.from_user else None

        # 如果用户正在等待输入
        if user_id and user_id in self.config.get("admins", []) and context.user_data.get('awaiting_input'):
            await self.handle_admin_input(update, context)
            return
        
        logger.info(f"转发模式: 收到消息来自频道: {chat_id} ({message.chat.title})")
        # 检查是否来自源频道
        if chat_id not in self.config['source_channels']:
            return
        
        content_type = self.get_message_type(message)
        if self.should_filter_message(message, content_type):
            logger.info(f"消息 {message.message_id} 被过滤，不进行转发。")
            return
        
        # 将消息传递给媒体组处理器
        await self.media_group_handler.add_message(message, self.forward_messages_group)

    async def handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
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
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加管理员: `{new_admin_id}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该用户已是管理员")
            elif action == 'add_source':
                channel_id = int(input_text)
                if channel_id not in self.config['source_channels']:
                    self.config['source_channels'].append(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加源频道: `{channel_id}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道已存在于源列表中")
            elif action == 'remove_source':
                channel_id = int(input_text)
                if channel_id in self.config['source_channels']:
                    self.config['source_channels'].remove(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除源频道: `{channel_id}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道不在源列表中")
            elif action == 'add_target':
                channel_id = int(input_text)
                if channel_id not in self.config['target_channels']:
                    self.config['target_channels'].append(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加目标频道: `{channel_id}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道已存在于目标列表中")
            elif action == 'remove_target':
                channel_id = int(input_text)
                if channel_id in self.config['target_channels']:
                    self.config['target_channels'].remove(channel_id)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除目标频道: `{channel_id}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该频道不在目标列表中")
            elif action == 'set_delay':
                delay = int(input_text)
                if delay < 0:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 延迟时间不能为负数")
                    return
                self.config['forward_settings']['delay_seconds'] = delay
                self.save_config()
                await context.bot.send_message(chat_id=chat_id, text=f"✅ 转发延迟已设置为 {delay} 秒")
            elif action == 'add_paraphrase_rule':
                rule_parts = input_text.split('=', 1)
                if len(rule_parts) == 2:
                    key = rule_parts[0].strip()
                    value = rule_parts[1].strip()
                    if key and value:
                        self.config['paraphrase_rules'][key] = value
                        self.save_config()
                        await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加伪原创规则: `{key}={value}`")
                    else:
                        await context.bot.send_message(chat_id=chat_id, text="❌ 伪原创规则格式不正确，请使用 `A=B` 格式。")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 伪原创规则格式不正确，请使用 `A=B` 格式。")
            elif action == 'remove_paraphrase_rule':
                key_to_remove = input_text.strip()
                if key_to_remove in self.config['paraphrase_rules']:
                    del self.config['paraphrase_rules'][key_to_remove]
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除伪原创规则: `{key_to_remove}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该伪原创规则不存在")
            elif action == 'add_keyword_filter': # Handle adding keyword filter
                keyword = input_text.strip()
                if keyword and keyword not in self.config['forward_settings']['keyword_filter']:
                    self.config['forward_settings']['keyword_filter'].append(keyword)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已添加过滤关键词: `{keyword}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 过滤关键词已存在或无效")
            elif action == 'remove_keyword_filter': # Handle removing keyword filter
                keyword_to_remove = input_text.strip()
                if keyword_to_remove in self.config['forward_settings']['keyword_filter']:
                    self.config['forward_settings']['keyword_filter'].remove(keyword_to_remove)
                    self.save_config()
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ 已移除过滤关键词: `{keyword_to_remove}`")
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ 该过滤关键词不存在")

        except ValueError:
            await context.bot.send_message(chat_id=chat_id, text="❌ 无效的输入，请提供一个有效的数字ID或URL。")
        except Exception as e:
            logger.error(f"处理管理员输入失败: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"❌ 处理请求时发生错误: {e}")
        finally:
            # 根据最后一个菜单状态刷新面板 - 移除采集相关
            if context.user_data.get('last_menu') == "admin_management":
                await self.send_admin_management_panel(chat_id, context)
            elif context.user_data.get('last_menu') == "forward_settings":
                await self.send_forward_settings_panel(chat_id, context)
            elif context.user_data.get('last_menu') == "paraphrase_settings":
                await self.send_paraphrase_settings_panel(chat_id, context)
            elif context.user_data.get('last_menu') == "keyword_filter": # Refresh keyword filter menu
                await self.send_keyword_filter_panel(chat_id, context)
            else:
                await self.send_admin_panel(chat_id, context)
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """状态命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        uptime = datetime.now() - self.stats['start_time']
        
        # 转义特殊字符
        uptime_str = escape_markdown_v2(str(uptime).split('.')[0])
        
        status_text = f"""📊 *机器人状态*

🕐 *运行时间:* {uptime_str}
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
• 保留发送者: {'✅' if self.config['forward_settings']['preserve_sender'] else '❌'}
• 媒体组超时: {self.config['forward_settings']['media_group_timeout']}秒"""
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """统计命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        # 从数据库获取详细统计
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 今日转发统计
        cursor.execute('''
            SELECT COUNT(*), content_type, COALESCE(is_media_group, 0) as is_media_group
            FROM forward_logs 
            WHERE DATE(timestamp) = DATE('now')
            GROUP BY content_type, is_media_group
        ''')
        today_stats = cursor.fetchall()
        
        # 成功率统计
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
            for count, content_type, is_media_group in today_stats:
                content_type_safe = escape_markdown_v2(content_type or '未知')
                group_indicator = " \\(媒体组\\)" if is_media_group else ""
                stats_text += f"• {content_type_safe}{group_indicator}: {count}条\n"
        
        if success_stats and success_stats[0] > 0:
            success_rate = (success_stats[1] / success_stats[0]) * 100
            stats_text += f"\n✅ *今日成功率:* {success_rate:.1f}%"
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def add_source_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加源频道命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供频道ID\n用法: `/add_source -1001234567890`")
            return
        
        try:
            channel_id = int(context.args[0])
            if channel_id not in self.config['source_channels']:
                self.config['source_channels'].append(channel_id)
                self.save_config()
                await update.message.reply_text(f"✅ 已添加源频道: `{channel_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text("❌ 该频道已存在于源列表中")
        except ValueError:
            await update.message.reply_text("❌ 无效的频道ID")
    
    async def add_target_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加目标频道命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供频道ID\n用法: `/add_target -1001234567890`")
            return
        
        try:
            channel_id = int(context.args[0])
            if channel_id not in self.config['target_channels']:
                self.config['target_channels'].append(channel_id)
                self.save_config()
                await update.message.reply_text(f"✅ 已添加目标频道: `{channel_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text("❌ 该频道已存在于目标列表中")
        except ValueError:
            await update.message.reply_text("❌ 无效的频道ID")
    
    async def remove_source_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """移除源频道命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供频道ID\n用法: `/remove_source -1001234567890`")
            return
        
        try:
            channel_id = int(context.args[0])
            if channel_id in self.config['source_channels']:
                self.config['source_channels'].remove(channel_id)
                self.save_config()
                await update.message.reply_text(f"✅ 已移除源频道: `{channel_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text("❌ 该频道不在源列表中")
        except ValueError:
            await update.message.reply_text("❌ 无效的频道ID")
    
    async def remove_target_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """移除目标频道命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供频道ID\n用法: `/remove_target -1001234567890`")
            return
        
        try:
            channel_id = int(context.args[0])
            if channel_id in self.config['target_channels']:
                self.config['target_channels'].remove(channel_id)
                self.save_config()
                await update.message.reply_text(f"✅ 已移除目标频道: `{channel_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text("❌ 该频道不在目标列表中")
        except ValueError:
            await update.message.reply_text("❌ 无效的频道ID")
    
    async def list_sources_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """列出源频道"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        sources = self.config['source_channels']
        if not sources:
            await update.message.reply_text("📢 当前没有配置源频道")
            return
        
        text = "📢 *源频道列表:*\n\n"
        for i, source_id in enumerate(sources, 1):
            text += f"{i}\\. `{source_id}`\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def list_targets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """列出目标频道"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        targets = self.config['target_channels']
        if not targets:
            await update.message.reply_text("🎯 当前没有配置目标频道")
            return
        
        text = "🎯 *目标频道列表:*\n\n"
        for i, target_id in enumerate(targets, 1):
            text += f"{i}\\. `{target_id}`\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加管理员命令"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供用户ID\n用法: `/add_admin 123456789`")
            return
        
        try:
            new_admin_id = int(context.args[0])
            if new_admin_id not in self.config['admins']:
                self.config['admins'].append(new_admin_id)
                self.save_config()
                await update.message.reply_text(f"✅ 已添加管理员: `{new_admin_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text("❌ 该用户已是管理员")
        except ValueError:
            await update.message.reply_text("❌ 无效的用户ID")
    
    async def list_admins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """列出管理员"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        admins = self.config['admins']
        if not admins:
            await update.message.reply_text("👥 当前没有配置管理员")
            return
        
        text = "👥 *管理员列表:*\n\n"
        for i, admin_id in enumerate(admins, 1):
            text += f"{i}\\. `{admin_id}`\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查看设置"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        settings = self.config['forward_settings']
        
        # 安全处理过滤器列表
        filter_types = escape_markdown_v2(', '.join(settings['filter_content_types']) if settings['filter_content_types'] else '无')
        keyword_filters = escape_markdown_v2(', '.join(settings['keyword_filter']) if settings['keyword_filter'] else '无')
        
        settings_text = f"""⚙️ *当前设置*

🕐 *转发延迟:* {settings['delay_seconds']} 秒
📝 *显示来源信息:* {'✅' if settings['add_source_info'] else '❌'}
👤 *保留发送者信息:* {'✅' if settings['preserve_sender'] else '❌'}
🚫 *过滤的内容类型:* {filter_types}
🔑 *关键词过滤:* {keyword_filters}
⚡ *每分钟最大转发数:* {settings['max_forwards_per_minute']}
🖼️ *媒体组超时:* {settings['media_group_timeout']} 秒

📊 *通知设置:*
• 错误通知: {'✅' if self.config['notification_settings']['notify_admin_on_error'] else '❌'}
• 日报: {'✅' if self.config['notification_settings']['daily_report'] else '❌'}"""
        
        await update.message.reply_text(settings_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    async def set_delay_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """设置转发延迟"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        if not context.args:
            await update.message.reply_text("❌ 请提供延迟秒数\n用法: `/set_delay 5`")
            return
        
        try:
            delay = int(context.args[0])
            if delay < 0:
                await update.message.reply_text("❌ 延迟时间不能为负数")
                return
            
            self.config['forward_settings']['delay_seconds'] = delay
            self.save_config()
            await update.message.reply_text(f"✅ 转发延迟已设置为 {delay} 秒")
        except ValueError:
            await update.message.reply_text("❌ 无效的数字")
    
    async def toggle_source_info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """切换来源信息显示"""
        user_id = update.effective_user.id
        if not await self.is_admin(user_id):
            return
        
        current = self.config['forward_settings']['add_source_info']
        self.config['forward_settings']['add_source_info'] = not current
        self.save_config()
        
        status = "开启" if not current else "关闭"
        await update.message.reply_text(f"✅ 来源信息显示已{status}")
    
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
        logger.info(f"检查消息是否需要过滤。消息ID: {message.message_id}, 内容类型: {content_type}")
        # 内容类型过滤
        if content_type in self.config['forward_settings']['filter_content_types']:
            logger.info(f"消息被内容类型过滤: {content_type}")
            return True
        
        # 关键词过滤
        message_content = message.text or message.caption
        if message_content and self.config['forward_settings']['keyword_filter']:
            text_lower = message_content.lower()
            logger.info(f"检查关键词过滤。消息文本: '{text_lower}', 过滤关键词: {self.config['forward_settings']['keyword_filter']}")
            for keyword in self.config['forward_settings']['keyword_filter']:
                if keyword.lower() in text_lower:
                    logger.info(f"消息被关键词过滤: '{keyword}' in '{message_content}'")
                    return True
        
        logger.info(f"消息 {message.message_id} 未被过滤。")
        return False
    
    async def forward_messages_group(self, messages: List[Message]):
        """转发消息组（支持媒体组）"""
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
            logger.info(f"转发媒体组，包含 {len(messages)} 条消息")
            await self.forward_media_group(messages)
        else:
            logger.info(f"转发单条消息")
            await self.forward_single_message(messages[0])
    
    async def forward_media_group(self, messages: List[Message]):
        """转发媒体组"""
        targets = self.config['target_channels']
        
        for target_id in targets:
            try:
                # 构建媒体组
                media_list = []
                caption_text = None
                
                # 先构建caption
                if self.config['forward_settings']['add_source_info']:
                    caption_text = await self.build_caption(messages[0])
                
                for i, message in enumerate(messages):
                    # 只在第一条消息添加caption
                    if i == 0 and caption_text:
                        input_media = self.create_input_media(message, caption_text)
                    else:
                        input_media = self.create_input_media(message)
                    
                    if input_media:
                        media_list.append(input_media)
                
                if media_list:
                    # 发送媒体组
                    sent_messages = await self.application.bot.send_media_group(
                        chat_id=target_id,
                        media=media_list
                    )
                    
                    # 记录成功转发
                    for i, (original_msg, sent_msg) in enumerate(zip(messages, sent_messages)):
                        content_type = self.get_message_type(original_msg)
                        self.log_forward(
                            original_msg.chat_id,
                            target_id,
                            original_msg.message_id,
                            sent_msg.message_id,
                            content_type,
                            original_msg.media_group_id,
                            True,
                            True,
                            None
                        )
                    
                    self.stats['messages_forwarded'] += len(messages)
                    self.stats['media_groups_forwarded'] += 1
                    logger.info(f"媒体组已转发: {messages[0].chat_id} -> {target_id} ({len(messages)}条)")
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"媒体组转发失败 {messages[0].chat_id} -> {target_id}: {error_msg}")
                
                # 记录失败转发
                for message in messages:
                    content_type = self.get_message_type(message)
                    self.log_forward(
                        message.chat_id,
                        target_id,
                        message.message_id,
                        None,
                        content_type,
                        message.media_group_id,
                        True,
                        False,
                        error_msg
                    )
                
                self.stats['failed_forwards'] += len(messages)
                
                # 通知管理员
                if self.config['notification_settings']['notify_admin_on_error']:
                    await self.notify_admins_error(messages[0], target_id, error_msg)
    
    async def forward_single_message(self, message: Message):
        """转发单条消息"""
        targets = self.config['target_channels']
        content_type = self.get_message_type(message)
        
        for target_id in targets:
            try:
                # 构建标题，如果需要的话
                caption = None
                if self.config['forward_settings']['add_source_info']:
                    caption = await self.build_caption(message)
                
                # 使用 copy_message 转发单条消息
                forwarded_message = await self.application.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                    caption=caption
                )
                
                # 记录成功转发
                self.log_forward(
                    message.chat_id,
                    target_id,
                    message.message_id,
                    forwarded_message.message_id,
                    content_type,
                    message.media_group_id,
                    False,
                    True,
                    None
                )
                
                self.stats['messages_forwarded'] += 1
                logger.info(f"消息已转发: {message.chat_id} -> {target_id}")
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"转发失败 {message.chat_id} -> {target_id}: {error_msg}")
                
                # 记录失败转发
                self.log_forward(
                    message.chat_id,
                    target_id,
                    message.message_id,
                    None,
                    content_type,
                    message.media_group_id,
                    False,
                    False,
                    error_msg
                )
                
                self.stats['failed_forwards'] += 1
                
                # 通知管理员
                if self.config['notification_settings']['notify_admin_on_error']:
                    await self.notify_admins_error(message, target_id, error_msg)
    
    def create_input_media(self, message: Message, caption: str = None):
        """根据消息创建InputMedia对象，可选择性添加caption"""
        try:
            if message.photo:
                # 获取最高质量的照片
                photo = message.photo[-1]
                return InputMediaPhoto(media=photo.file_id, caption=caption)
            elif message.video:
                return InputMediaVideo(media=message.video.file_id, caption=caption)
            elif message.document:
                return InputMediaDocument(media=message.document.file_id, caption=caption)
            elif message.audio:
                return InputMediaAudio(media=message.audio.file_id, caption=caption)
            else:
                logger.warning(f"不支持的媒体类型: {self.get_message_type(message)}")
                return None
        except Exception as e:
            logger.error(f"创建InputMedia失败: {e}")
            return None
    
    async def build_caption(self, message: Message) -> str:
        """构建转发消息的说明"""
        original_caption = message.caption or ""
        logger.info(f"build_caption: 初始原始文本: {original_caption}")
        
        # 1. 移除表情符号 (根据用户反馈，此功能可能导致中文文本丢失，暂时移除)
        # original_caption = self.remove_emojis(original_caption)
        # logger.info(f"build_caption: 删除表情符号后文本: {original_caption}")

        # 2. 应用伪原创替换规则
        original_caption = self.apply_paraphrase_rules(original_caption)
        
        if not self.config['forward_settings']['add_source_info']:
            return original_caption
        
        # 添加来源信息，使用普通文本格式
        chat_title = message.chat.title or str(message.chat.id)
        source_info = f""
        
        if message.from_user and self.config['forward_settings']['preserve_sender']:
            sender_name = message.from_user.full_name
            source_info += f""
        
        # 使用简单的时间格式，避免 Windows 格式化问题
        time_str = message.date.strftime('%Y-%m-%d %H:%M:%S')
        source_info += f""   
        
        return original_caption + source_info   
    
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
        """通知管理员转发错误 - 使用普通文本避免格式问题"""
        chat_title = message.chat.title or str(message.chat.id)
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        error_text = f"""❌ 转发失败通知

源频道: {chat_title}
目标频道: {target_id}
错误信息: {error_msg}
时间: {time_str}"""
        
        for admin_id in self.config['admins']:
            try:
                await self.application.bot.send_message(
                    chat_id=admin_id,
                    text=error_text
                )
            except Exception as e:
                logger.error(f"通知管理员失败 {admin_id}: {e}")
    
    def apply_paraphrase_rules(self, text: str) -> str:
        """应用伪原创替换规则"""
        rules = self.config.get('paraphrase_rules', {})
        logger.info(f"开始应用伪原创规则。原始文本: {text}")
        if not rules:
            logger.info("没有配置伪原创规则。")
            return text

        modified_text = text
        for old_word, new_word in rules.items():
            logger.info(f"应用规则: '{old_word}' -> '{new_word}'")
            modified_text = modified_text.replace(old_word, new_word)
            logger.info(f"替换后文本: {modified_text}")
        logger.info(f"伪原创规则应用完成。最终文本: {modified_text}")
        return modified_text

    def run(self):
        """运行机器人"""
        logger.info("机器人启动中...")
        # 更新媒体组处理器超时时间
        self.media_group_handler.timeout_seconds = self.config['forward_settings']['media_group_timeout']
        self.application.run_polling()

if __name__ == "__main__":
    # 从配置文件获取token
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

    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ 请在 bot_config.json 中设置有效的 bot_token")
        print("💡 示例: \"bot_token\": \"your_bot_token_here\"")
        exit(1)
    
    # 创建并运行机器人
    bot = TelegramForwardBot(TOKEN)
    bot.run()
