import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
import aiohttp
from maxapi import Bot, Dispatcher, F
from maxapi.types import (
    MessageCreated,
    MessageCallback,
    InputMedia,
    CallbackButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
# ==========================================
# НАСТРОЙКИ
# ==========================================
load_dotenv()
MAX_TOKEN = os.environ["MAX_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
ADMIN_ID = {25841956,64340040,136178450}

STATE_FILE = "state.json"
TEMPLATES_DIR = "templates"
os.makedirs(TEMPLATES_DIR, exist_ok=True)

TZ = timezone(timedelta(hours=5))

AUTOPOST_HOUR = 8

bot = Bot(token=MAX_TOKEN)
dp = Dispatcher()

# ==========================================
# РАБОТА С СОХРАНЕНИЕМ СОСТОЯНИЯ
# ==========================================
def load_state() -> dict:
    default_state = {
        "message_ids": [],
        "auto_delete": True,
    }

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                default_state.update(data)

        except (OSError, json.JSONDecodeError):
            pass

    return default_state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=4)


def load_last_messages() -> list[str]:
    ids = load_state().get("message_ids", [])

    if not isinstance(ids, list):
        return []

    return [str(message_id) for message_id in ids]


def save_last_messages(message_ids: list[str]) -> None:
    state = load_state()

    # Убираем повторяющиеся ID
    state["message_ids"] = list(dict.fromkeys(
        str(message_id) for message_id in message_ids
    ))

    save_state(state)


def is_auto_delete_enabled() -> bool:
    return bool(load_state().get("auto_delete", True))


def set_auto_delete(enabled: bool) -> None:
    state = load_state()
    state["auto_delete"] = enabled
    save_state(state)


def is_autopost_enabled() -> bool:
    """Проверяет, включен ли автопостинг."""
    return bool(load_state().get("autopost_enabled", False))


def set_autopost_enabled(enabled: bool) -> None:
    """Включает или выключает автопостинг."""
    state = load_state()
    state["autopost_enabled"] = enabled
    save_state(state)


# ==========================================
# ШАБЛОНЫ НЕДЕЛИ
# ==========================================
WEEKDAYS = [
    ("monday", "Понедельник"),
    ("tuesday", "Вторник"),
    ("wednesday", "Среда"),
    ("thursday", "Четверг"),
    ("friday", "Пятница"),
]


def load_template_index() -> int:
    """Получает текущий индекс выбранного дня недели из состояния."""
    return load_state().get("template_index", 0) % len(WEEKDAYS)


def save_template_index(index: int) -> None:
    """Сохраняет текущий индекс дня недели."""
    state = load_state()
    state["template_index"] = index % len(WEEKDAYS)
    save_state(state)


def set_waiting_for_template(day_key: str | None) -> None:
    """Устанавливает режим ожидания загрузки фото для конкретного дня."""
    state = load_state()
    state["waiting_for_template"] = day_key
    save_state(state)


def get_waiting_for_template() -> str | None:
    """Проверяет, ждет ли бот фото для какого-то дня."""
    return load_state().get("waiting_for_template", None)


# ==========================================
# КЛАВИАТУРЫ И ТЕКСТЫ
# ==========================================
MAIN_MENU_TEXT = (
    "🏠 Главное меню бота\n"
    "Просто отправь мне фотографию и я ее отправлю😊\n\n"
    "Выберите нужный раздел:"
)
CONTROL_PANEL_TEXT = "Это панель управления❤️\n Выберите задачу:"


def build_main_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        CallbackButton(text="🛹Панель управления", payload="control_panel"),
        CallbackButton(text="⚙️ Настройки", payload="open_settings"),
    )
    return [keyboard.as_markup()]


def control_panel_keyboard():
    keyboard = InlineKeyboardBuilder()

    autopost_status = (
        "✅ Автопостинг ВКЛ" if is_autopost_enabled() else "❌ Автопостинг ВЫКЛ"
    )

    keyboard.row(
        CallbackButton(text="🛹 Шаблоны недели", payload="shablone"),
        CallbackButton(text=autopost_status, payload="toggle_autopost"),
    )
    keyboard.row(
        CallbackButton(text="⚙️ Настройки", payload="open_settings"),
        CallbackButton(text="⬅️ Главное меню", payload="back_to_main"),
    )
    return [keyboard.as_markup()]


def build_settings_keyboard():
    keyboard = InlineKeyboardBuilder()

    if is_auto_delete_enabled():
        button_text = "❌ Выключить автоудаление"
    else:
        button_text = "✅ Включить автоудаление"

    keyboard.row(CallbackButton(text=button_text, payload="toggle_auto_delete"))
    keyboard.row(CallbackButton(text="⬅️ Назад", payload="back_to_main"))

    return [keyboard.as_markup()]


def settings_text() -> str:
    status = "✅ ВКЛЮЧЕНО" if is_auto_delete_enabled() else "❌ ВЫКЛЮЧЕНО"

    return (
        "⚙️ Управление ботом\n\n"
        f"Автоматическое удаление: {status}\n\n"
        "Выберите нужный режим кнопками ниже."
    )


def button_week():
    idx = load_template_index()
    day_key, day_rus = WEEKDAYS[idx]

    # Проверяем, есть ли уже сохраненный файл шаблона
    file_path = os.path.join(TEMPLATES_DIR, f"{day_key}.jpg")
    status_icon = "✅" if os.path.exists(file_path) else "❌"

    keyboard = InlineKeyboardBuilder()

    # Кнопки навигации по дням
    keyboard.row(
        CallbackButton(text="⏪ Назад", payload="tmpl_prev"),
        CallbackButton(text=f"{status_icon} {day_rus}", payload="tmpl_current"),
        CallbackButton(text="Вперед ⏩", payload="tmpl_next"),
    )
    # Кнопка действия и выхода
    keyboard.row(
        CallbackButton(text="🔄 Заменить / Добавить фото", payload="tmpl_replace"),
        CallbackButton(text="⬅️ В панель управления", payload="control_panel"),
    )
    return [keyboard.as_markup()]


# ==========================================
# УДАЛЕНИЕ СТАРЫХ СООБЩЕНИЙ
# ==========================================
async def cleanup_old_photos() -> int:
    old_ids = load_last_messages()

    deleted_count = 0
    failed_ids = []

    for msg_id in old_ids:
        try:
            await bot.delete_message(message_id=msg_id)

            deleted_count += 1
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"Не удалось удалить сообщение {msg_id}: {e}")
            failed_ids.append(msg_id)

    # Сохраняем ID сообщений, которые не получилось удалить
    save_last_messages(failed_ids)

    return deleted_count


# ==========================================
# АВТОПОСТИНГ
# ==========================================
async def background_auto_poster():
    """Фоновая задача: раз в день постит шаблон текущего дня недели в канал."""
    print("🕒 Фоновый автопостинг запущен.")
    while True:
        try:
            await asyncio.sleep(60)

            if not is_autopost_enabled():
                continue

            now = datetime.now(TZ)

            if now.hour < AUTOPOST_HOUR:
                continue

            # Постим расписание на ЗАВТРА
            target = now + timedelta(days=1)

            # Если завтра суббота или воскресенье — шаблонов нет
            if target.weekday() >= len(WEEKDAYS):
                continue

            day_key, day_rus = WEEKDAYS[target.weekday()]
            file_path = os.path.join(TEMPLATES_DIR, f"{day_key}.jpg")
            target_str = target.strftime("%Y-%m-%d")

            if not os.path.exists(file_path):
                continue

            # Уже постили расписание на эту дату
            if load_state().get("last_posted_date") == target_str:
                continue

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"📅 Расписание на завтра ({day_rus}):",
                attachments=[InputMedia(path=file_path)],
            )

            # Перечитываем состояние после await, чтобы не затереть чужие изменения
            state = load_state()
            state["last_posted_date"] = target_str
            save_state(state)
            print(f"✅ Расписание на {day_rus} успешно отправлено в канал!")

        except Exception as e:
            print(f"❌ Ошибка в фоновом автопостинге: {e}")


# ==========================================
# КОМАНДА START
# ==========================================
@dp.message_created(F.message.body.text == "/start")
async def cmd_start(event: MessageCreated):
    if not event.message.sender:
        return

    if event.message.sender.user_id not in ADMIN_ID:
        return

    await event.message.answer(
        text=MAIN_MENU_TEXT,
        attachments=build_main_keyboard(),
    )


# ==========================================
# НАСТРОЙКИ
# ==========================================
@dp.message_callback(F.callback.payload == "toggle_auto_delete")
async def toggle_auto_delete(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    set_auto_delete(not is_auto_delete_enabled())

    await event.answer(
        new_text=settings_text(),
        attachments=build_settings_keyboard(),
    )


@dp.message_callback(F.callback.payload == "open_settings")
async def open_settings(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    await event.answer(
        new_text=settings_text(),
        attachments=build_settings_keyboard(),
    )


# ==========================================
# ПАНЕЛЬ УПРАВЛЕНИЯ
# ==========================================
@dp.message_callback(F.callback.payload == "control_panel")
async def control_panel(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    set_waiting_for_template(None)

    await event.answer(
        new_text=CONTROL_PANEL_TEXT,
        attachments=control_panel_keyboard(),
    )


@dp.message_callback(F.callback.payload == "toggle_autopost")
async def toggle_autopost(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    new_state = not is_autopost_enabled()
    set_autopost_enabled(new_state)

    status_text = "включен ✅" if new_state else "выключен ❌"

    await event.answer(
        notification=f"Автопостинг {status_text}",
        new_text=CONTROL_PANEL_TEXT,
        attachments=control_panel_keyboard(),
    )


# ==========================================
# ШАБЛОНЫ НЕДЕЛИ (ИНТЕРФЕЙС)
# ==========================================
async def render_template_card(event: MessageCallback):
    idx = load_template_index()
    day_key, day_rus = WEEKDAYS[idx]

    photo_path = os.path.join(TEMPLATES_DIR, f"{day_key}.jpg")

    attachments = list(button_week())

    if os.path.exists(photo_path):
        # Если фото для этого дня сохранено — добавляем его в начало списка вложений
        attachments.insert(0, InputMedia(path=photo_path))
        text = (
            f"📅 Шаблон недели: **{day_rus}**\n"
            "✅ Фото установлено.\n\n"
            "Используйте кнопки ниже для навигации:"
        )
    else:
        text = (
            f"📅 Шаблон недели: **{day_rus}**\n"
            "❌ Фото не задано.\n\n"
            "Используйте кнопки ниже для навигации:"
        )

    await event.answer(new_text=text, attachments=attachments)


@dp.message_callback(F.callback.payload == "shablone")
async def shablone(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    set_waiting_for_template(None)
    await render_template_card(event)


@dp.message_callback(F.callback.payload == "tmpl_prev")
async def tmpl_prev(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        return
    set_waiting_for_template(None)
    save_template_index(load_template_index() - 1)
    await render_template_card(event)


@dp.message_callback(F.callback.payload == "tmpl_next")
async def tmpl_next(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        return
    set_waiting_for_template(None)
    save_template_index(load_template_index() + 1)
    await render_template_card(event)


@dp.message_callback(F.callback.payload == "tmpl_current")
async def tmpl_current(event: MessageCallback):
    """Кнопка с названием дня: просто показывает статус, ничего не меняет."""
    if event.callback.user.user_id not in ADMIN_ID:
        return

    day_key, day_rus = WEEKDAYS[load_template_index()]
    has_photo = os.path.exists(os.path.join(TEMPLATES_DIR, f"{day_key}.jpg"))
    status = "фото установлено" if has_photo else "фото не задано"

    await event.answer(notification=f"{day_rus}: {status}")


@dp.message_callback(F.callback.payload == "tmpl_replace")
async def tmpl_replace(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        return

    day_key, day_rus = WEEKDAYS[load_template_index()]
    set_waiting_for_template(day_key)

    await event.answer(
        notification=f"Отправьте фотографию для дня: {day_rus}",
        new_text=(
            "📥 Отправьте в чат **одну фотографию**, которая будет установлена"
            f" как шаблон для дня: **{day_rus}**."
        ),
        attachments=button_week(),
    )


# ==========================================
# ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# ==========================================
@dp.message_callback(F.callback.payload == "back_to_main")
async def back_to_main(event: MessageCallback):
    if event.callback.user.user_id not in ADMIN_ID:
        await event.answer(notification="Нет доступа!")
        return

    set_waiting_for_template(None)

    await event.answer(
        new_text=MAIN_MENU_TEXT,
        attachments=build_main_keyboard(),
    )


# ==========================================
# ОБРАБОТКА ФОТОГРАФИЙ
# ==========================================
@dp.message_created()
async def handle_photos(event: MessageCreated):
    if not event.message.sender:
        return

    if event.message.sender.user_id not in ADMIN_ID:
        return

    attachments = event.message.body.attachments or []

    image_urls = [
        att.payload.url
        for att in attachments
        if att.type == "image"
        and getattr(att.payload, "url", None)
    ]

    if not image_urls:
        return

    # --- ПРОВЕРКА: ЖДЕМ ЛИ МЫ ШАБЛОН ДНЯ НЕДЕЛИ? ---
    waiting_day = get_waiting_for_template()
    if waiting_day:
        img_url = image_urls[0]
        file_path = os.path.join(TEMPLATES_DIR, f"{waiting_day}.jpg")

        saved = False
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url) as resp:
                if resp.status == 200:
                    with open(file_path, "wb") as f:
                        f.write(await resp.read())
                    saved = True

        if not saved:
            await event.message.answer(
                text="❌ Не удалось скачать фотографию, попробуйте ещё раз.",
                attachments=button_week(),
            )
            return

        set_waiting_for_template(None)

        day_rus = next(rus for eng, rus in WEEKDAYS if eng == waiting_day)

        await event.message.answer(
            text=f"✅ Шаблон для дня **{day_rus}** успешно сохранен!",
            attachments=button_week(),
        )
        return
    # -----------------------------------------------

    caption = event.message.body.text or ""

    temp_files = []
    media_list = []

    try:
        # Скачиваем новые фотографии
        async with aiohttp.ClientSession() as session:
            for url in image_urls:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        print(f"Ошибка загрузки изображения: {resp.status}")
                        continue

                    data = await resp.read()

                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    tmp.write(data)
                    tmp.close()

                    temp_files.append(tmp.name)
                    media_list.append(InputMedia(path=tmp.name))

        if not media_list:
            return

        # Публикуем новые фотографии
        sent_msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=caption,
            attachments=media_list,
        )

        new_message_ids = []

        sent_message = getattr(sent_msg, "message", None)
        sent_body = getattr(sent_message, "body", None)
        message_id = getattr(sent_body, "mid", None)

        if message_id is not None:
            new_message_ids.append(str(message_id))

        if sent_msg is not None:
            if is_auto_delete_enabled():
                # Удаляем предыдущие публикации
                deleted = await cleanup_old_photos()

                # Сохраняем новые ID вместе с теми, удаление которых не удалось
                remaining_ids = load_last_messages()
                save_last_messages(remaining_ids + new_message_ids)

                await event.message.answer(
                    f"✅ Фотографии опубликованы!\n"
                    f"Удалено старых сообщений: {deleted}"
                )

            else:
                # Автоудаление выключено — просто накапливаем ID
                old_ids = load_last_messages()
                save_last_messages(old_ids + new_message_ids)

                await event.message.answer(
                    "✅ Фотографии опубликованы!\n"
                    "Автоудаление выключено."
                )

    except Exception as e:
        print(f"Ошибка обработки фотографий: {e}")

        await event.message.answer(
            "❌ Произошла ошибка при публикации фотографий."
        )

    finally:
        # Удаляем временные файлы
        for path in temp_files:
            if os.path.exists(path):
                os.remove(path)


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    await bot.delete_webhook()

    # Запускаем фоновый автопостинг параллельно с ботом
    # (ссылку сохраняем, чтобы задачу не собрал сборщик мусора)
    autoposter_task = asyncio.create_task(background_auto_poster())

    print("Бот успешно запущен!")

    try:
        await dp.start_polling(bot)
    finally:
        autoposter_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
