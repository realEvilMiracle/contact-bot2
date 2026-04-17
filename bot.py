import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaPhoto,
    InputTextMessageContent,
    Update,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONTACTS_PATH = BASE_DIR / "contacts.json"


def load_contacts(path: Path = CONTACTS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CONTACTS: dict = load_contacts()


def get_admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_IDS", "")
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.add(int(chunk))
    return ids


ADMIN_IDS = get_admin_ids()


# ---------------------------------------------------------------------------
# Рендеринг
# ---------------------------------------------------------------------------

def build_main_menu_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(category["name"], callback_data=f"cat|{cat_id}")]
        for cat_id, category in CONTACTS.items()
    ]
    keyboard.append([InlineKeyboardButton("🔎 Поиск", switch_inline_query_current_chat="")])
    return InlineKeyboardMarkup(keyboard)


def build_category_markup(cat_id: str, category: dict) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(c["name"], callback_data=f"con|{cat_id}|{contact_id}")]
        for contact_id, c in category["contacts"].items()
    ]
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def build_contact_markup(cat_id: str, contact_id: str) -> InlineKeyboardMarkup:
    share_query = f"id:{cat_id}:{contact_id}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↗️ Поделиться", switch_inline_query=share_query)],
            [InlineKeyboardButton("◀️ К категории", callback_data=f"cat|{cat_id}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="main")],
        ]
    )


def format_contact_text(contact: dict) -> str:
    lines = [f"📌 <b>{contact['name']}</b>"]
    if desc := contact.get("description"):
        lines.append(desc)
    if link := contact.get("phone") or contact.get("link"):
        lines.append(f'🔗 <a href="{link}">Открыть контакт</a>')
    return "\n".join(lines)


def resolve_photo(photo: str | None):
    """Возвращает объект, пригодный для передачи в Telegram, либо None."""
    if not photo:
        return None
    if photo.startswith(("http://", "https://")):
        return photo
    path = BASE_DIR / photo
    if path.is_file():
        return path.open("rb")
    logger.warning("Фото не найдено: %s", path)
    return None


# ---------------------------------------------------------------------------
# Навигация
# ---------------------------------------------------------------------------

async def send_main_menu(update: Update, edit: bool = False) -> None:
    user = update.effective_user
    text = f"Привет, {user.first_name}! На связи команда «Октября»!\nВ этом чат-боте мы собрали для тебя полезные контакты для твоей свадьбы. Каждая кнопка откроет для тебя подборку ведущих, фотографов, видеографов, рилс-мейкеров, кондитеров и организаторов.\nДелись ботом и подписывайся на сообщество «Октября», мы готовим для тебя еще много всего интересного!"
    markup = build_main_menu_markup()

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=text, reply_markup=markup)
            return
        except BadRequest:
            await update.callback_query.message.delete()
            await update.callback_query.message.reply_text(text=text, reply_markup=markup)
            return

    await update.message.reply_text(text=text, reply_markup=markup)


async def show_category(query, cat_id: str) -> None:
    category = CONTACTS.get(cat_id)
    if not category:
        await query.answer("Категория не найдена", show_alert=True)
        return

    text = f"🔹 {category['name']}\nВыберите контакт:"
    markup = build_category_markup(cat_id, category)
    try:
        await query.edit_message_text(text=text, reply_markup=markup)
    except BadRequest:
        await query.message.delete()
        await query.message.reply_text(text=text, reply_markup=markup)


async def show_contact(query, cat_id: str, contact_id: str) -> None:
    category = CONTACTS.get(cat_id)
    contact = category and category["contacts"].get(contact_id)
    if not contact:
        await query.answer("Контакт не найден", show_alert=True)
        return

    text = format_contact_text(contact)
    markup = build_contact_markup(cat_id, contact_id)
    photo = resolve_photo(contact.get("photo"))

    try:
        if photo:
            try:
                await query.edit_message_media(
                    InputMediaPhoto(photo, caption=text, parse_mode=ParseMode.HTML),
                    reply_markup=markup,
                )
                return
            except BadRequest:
                await query.message.delete()
                await query.message.reply_photo(
                    photo=photo, caption=text, reply_markup=markup, parse_mode=ParseMode.HTML
                )
                return

        try:
            await query.edit_message_text(
                text=text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except BadRequest:
            await query.message.delete()
            await query.message.reply_text(
                text=text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
    finally:
        if hasattr(photo, "close"):
            try:
                photo.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------

def iter_contacts():
    for cat_id, category in CONTACTS.items():
        for contact_id, contact in category["contacts"].items():
            yield cat_id, contact_id, category, contact


def search_contacts(query: str, limit: int = 30) -> list[tuple[str, str, dict, dict]]:
    q = query.strip().lower()
    results = []
    for cat_id, contact_id, category, contact in iter_contacts():
        if not q or q in contact["name"].lower() or q in category["name"].lower():
            results.append((cat_id, contact_id, category, contact))
            if len(results) >= limit:
                break
    return results


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(update)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🤖 <b>Как пользоваться ботом</b>\n\n"
        "• /start — главное меню с категориями\n"
        "• /search &lt;имя&gt; — быстрый поиск по имени или категории\n"
        "• Кнопка «🔎 Поиск» — inline-поиск прямо в чате\n"
        "• На карточке контакта есть «↗️ Поделиться» — отправить друзьям\n"
    )
    if update.effective_user.id in ADMIN_IDS:
        text += "\n<b>Админ:</b>\n• /reload — перечитать contacts.json без рестарта"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "Использование: /search <имя>\nНапример: /search Денис"
        )
        return

    results = search_contacts(query, limit=20)
    if not results:
        await update.message.reply_text(f"По запросу «{query}» ничего не найдено.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"{contact['name']} — {category['name']}",
            callback_data=f"con|{cat_id}|{contact_id}",
        )]
        for cat_id, contact_id, category, contact in results
    ]
    buttons.append([InlineKeyboardButton("🏠 В меню", callback_data="main")])
    await update.message.reply_text(
        f"🔎 Результаты по «{query}»:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    global CONTACTS
    try:
        CONTACTS = load_contacts()
    except Exception as exc:
        logger.exception("Ошибка перезагрузки contacts.json")
        await update.message.reply_text(f"❌ Ошибка: {exc}")
        return
    total = sum(len(c["contacts"]) for c in CONTACTS.values())
    await update.message.reply_text(
        f"✅ Обновлено. Категорий: {len(CONTACTS)}, контактов: {total}."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    try:
        if data == "main":
            await send_main_menu(update, edit=True)
        elif data.startswith("cat|"):
            await show_category(query, data.split("|", 1)[1])
        elif data.startswith("cat_"):  # обратная совместимость со старыми сообщениями
            await show_category(query, data[4:])
        elif data.startswith("con|"):
            parts = data.split("|", 2)
            if len(parts) != 3:
                await query.answer("Некорректные данные кнопки", show_alert=True)
                return
            await show_contact(query, parts[1], parts[2])
        else:
            logger.warning("Неизвестный callback_data: %s", data)
    except Exception:
        logger.exception("Ошибка в button_handler")
        try:
            await query.message.reply_text("⚠️ Произошла ошибка. Попробуйте ещё раз.")
        except Exception:
            pass


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query or ""

    # Спец-формат "id:cat:contact" — открыть конкретный контакт
    if query.startswith("id:"):
        parts = query.split(":", 2)
        if len(parts) == 3:
            _, cat_id, contact_id = parts
            category = CONTACTS.get(cat_id)
            contact = category and category["contacts"].get(contact_id)
            if contact:
                results = [_contact_to_inline_result(cat_id, contact_id, category, contact)]
                await update.inline_query.answer(results, cache_time=5, is_personal=False)
                return

    found = search_contacts(query, limit=30)
    results = [
        _contact_to_inline_result(cat_id, contact_id, category, contact)
        for cat_id, contact_id, category, contact in found
    ]
    await update.inline_query.answer(results, cache_time=5, is_personal=False)


def _contact_to_inline_result(
    cat_id: str, contact_id: str, category: dict, contact: dict
) -> InlineQueryResultArticle:
    text = format_contact_text(contact)
    return InlineQueryResultArticle(
        id=f"{cat_id}:{contact_id}:{uuid4().hex[:6]}",
        title=contact["name"],
        description=category["name"],
        input_message_content=InputTextMessageContent(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        ),
    )


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return
    results = search_contacts(text, limit=10)
    if not results:
        await update.message.reply_text(
            "Не понял. Нажмите /start или воспользуйтесь /search."
        )
        return
    buttons = [
        [InlineKeyboardButton(
            f"{contact['name']} — {category['name']}",
            callback_data=f"con|{cat_id}|{contact_id}",
        )]
        for cat_id, contact_id, category, contact in results
    ]
    buttons.append([InlineKeyboardButton("🏠 В меню", callback_data="main")])
    await update.message.reply_text(
        f"🔎 Похоже, вы ищете:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_startup(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("search", "Поиск по имени"),
        BotCommand("help", "Помощь"),
    ])


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не задана. "
            "Добавьте её в файл .env или экспортируйте вручную."
        )

    application = (
        Application.builder()
        .token(token)
        .post_init(on_startup)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))
    application.run_polling()


if __name__ == "__main__":
    main()
