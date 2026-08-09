import os
import io
import re
import json
import base64
import asyncio
import logging

from PIL import Image
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from openai import AsyncOpenAI

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Check your .env file or deployment environment variables.")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY is missing. Check your .env file.")

openrouter_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

WASTE_LEVEL_EMOJI = {
    "minimal": "🌱",
    "low": "🙂",
    "moderate": "⚠️",
    "high": "🚨",
    "severe": "🆘",
}

ANALYSIS_PROMPT = """You are a food waste analyst. Look at this photo of food and/or drinks after a meal — it may be untouched, partly eaten/drunk, or leftovers.

How to break the photo into items — this matters a lot, read carefully:
- Treat everything on ONE plate/tray/container as a SINGLE combined item. Do NOT list its components separately (e.g. do not split a plate into "peas", "mashed potatoes", "pastry" — that is one plate, one item). Give it one short descriptive name covering the mix (e.g. "Roast Dinner (chicken, mash, peas, cranberry)") and judge how much of that WHOLE PLATE remains, holistically, as a fraction of what a full plate of that meal looks like. Do not estimate each component separately and add them up — look at the plate as a whole.
- EXCEPTION: if a plate/bowl/tray holds individually countable, identical whole units (e.g. apples, dumplings, chicken wings, nuggets, cookies, eggs, prawns), treat that as its own item and count the actual number of whole units left uneaten (fractions allowed for a partly-eaten unit, e.g. 2.5 apples if one apple is half-eaten). Use exact counts here, not a portion fraction.
- If there are multiple separate plates/trays (e.g. two people's meals, or a shared platter plus a separate rice bowl), give each its own single entry.
- Every distinct drink (cup, glass, bottle, can, pitcher) is its OWN separate entry — never merge a drink into a food entry, and never skip a drink just because the photo is food-focused.
- Do NOT include sauces, condiments, dips, or dressings as their own item (e.g. ketchup, chili sauce, mayo, soy sauce, salad dressing, gravy) — ignore them entirely, even if some is left on a plate.
- If the exact same item appears more than once (e.g. two identical drinks), combine into ONE entry and sum the wasted amount rather than listing duplicates.

For each item, work through these steps IN ORDER:
1. notes: in a few words, honestly describe exactly what is still visibly there — be concrete about quantity, e.g. "just scraps and sauce smears", "a few stray peas and a small chunk of pastry", "about a third of the rice still mounded up", "only a bite missing, rest untouched". Do NOT describe it vaguely — commit to a specific quantity impression first.
2. percent_remaining: derive this STRICTLY from your own notes above, using this scale:
   - notes = scraps / bones / crumbs / just sauce or gravy residue → 0-10%
   - notes = a few small/stray bits here and there → 10-25%
   - notes = roughly a third of it still there → 30-35%
   - notes = about half still there → 45-55%
   - notes = most of it still there, only a bite or two gone → 75-90%
   - notes = completely untouched / full → 100%
   IMPORTANT: judge total remaining VOLUME/AREA, not how many different food types still have a trace left. A plate where four different foods each have only a small bit remaining is still LOW percent_remaining overall (e.g. 15-25%) — do not inflate the number just because the plate "still has a bit of everything." Small residual amounts across many components should read as mostly-finished, not half-finished.
   - Mixed plate: judge the WHOLE plate's remaining fraction directly using the scale above.
   - Countable units: instead of a percentage, count the actual number of whole/partial units remaining.
   - Drinks: if you can't see the liquid level (capped, opaque, angled away), assume it's untouched/full (100%). Only use a lower number if you can clearly see the liquid line.
3. display: a short human-readable phrase for how much of THIS item is wasted — e.g. "2 apples", "1 apple", "0.5 portions", "1 portion", "1 cup". Use an exact count + the item's natural unit for countable items (apple, egg, dumpling, wing, nugget, cookie, prawn, etc.); use "portion(s)" for a mixed plate/dish; use "cup(s)" (or can/bottle/glass as appropriate) for drinks. Get singular vs plural right — "1 apple" not "1 apples".
4. portions_wasted: a normalized number derived from percent_remaining (or the count, for countable items) — roughly how many typical single-person portions the wasted amount in "display" is equivalent to (e.g. 2 apples ≈ 2, since one piece of fruit ≈ one serving; a plate at 20% remaining ≈ 0.25; one cup of a drink ≈ 1; a shared platter at 85% remaining with typical_servings 4 ≈ 3.5). This is only used to compute an overall total — it isn't shown to the user directly, so it just needs to be consistent with percent_remaining/count.
5. Sanity check before finalizing: re-read your own "tip" text (step below) against percent_remaining — they must agree. Never describe an item as "barely touched" or "nearly full" if percent_remaining is under 50%, and never describe it as "mostly finished" or "just a little left" if percent_remaining is over 50%.

Return a JSON object with this exact structure (include "notes" and "percent_remaining" per item — they document your reasoning and are never shown to the user, only "display" is shown):
{
  "items": [
    {"name": "Apples", "notes": "2 whole apples untouched", "percent_remaining": 100, "display": "2 apples", "portions_wasted": 2},
    {"name": "Roast Dinner (chicken, mash, peas, cranberry)", "notes": "a few stray peas, a small chunk of pastry, a little mash, mostly sauce smears", "percent_remaining": 20, "display": "0.25 portions", "portions_wasted": 0.25},
    {"name": "Iced Coffee", "notes": "capped, liquid level not visible", "percent_remaining": 100, "display": "1 cup", "portions_wasted": 1}
  ],
  "total_portions_wasted": 3.25,
  "waste_level": "moderate",
  "tip": "Most of the plate was finished, with just small bits of mash and pastry left — try a slightly smaller serving next time."
}

Rules:
- total_portions_wasted = sum of every item's portions_wasted, rounded to the nearest 0.25.
- waste_level must be exactly one of: "minimal", "low", "moderate", "high", "severe" — judge holistically from total_portions_wasted (roughly: under 0.5 = minimal, ~0.5-1 = low, ~1-2 = moderate, ~2-3 = high, 3+ = severe).
- tip must be a single, specific, actionable sentence about portioning, consistent with each item's notes/percent_remaining, referencing an item's "display" wasted amount where it makes the tip more concrete. Keep it under 30 words.
- If no food or drink is visible in the image at all, return {"items": [], "total_portions_wasted": 0, "waste_level": "minimal", "tip": "I couldn't spot any food or drink in this photo — try sending a clearer picture."}

Return ONLY the raw JSON object. No explanation, no markdown."""


def _fmt_portions(n: float) -> str:
    return f"{n:g}"


def merge_duplicate_items(items: list) -> list:
    """Collapse items with the same name (case-insensitive) into one, summing their portions."""
    merged: dict = {}
    order: list = []
    for item in items:
        name = (item.get("name") or "Item").strip()
        key = name.lower()
        if key not in merged:
            merged[key] = {"name": name, "display": item.get("display", ""), "portions_wasted": 0, "_count": 0}
            order.append(key)
        merged[key]["portions_wasted"] += item.get("portions_wasted", 0) or 0
        merged[key]["_count"] += 1

    result = []
    for key in order:
        m = merged[key]
        if m["_count"] > 1:
            m["display"] = f"~{_fmt_portions(m['portions_wasted'])} portions"
        result.append(m)
    return result


def build_report_message(parsed: dict) -> str:
    items = merge_duplicate_items(parsed.get("items", []))
    total_portions = parsed.get("total_portions_wasted", 0)
    level = parsed.get("waste_level", "minimal")
    tip = parsed.get("tip", "")
    emoji = WASTE_LEVEL_EMOJI.get(level, "🍽️")

    lines = ["🍽️ *Food Waste Report*", "━━━━━━━━━━━━━━━━━━━━━"]

    if items:
        for item in items:
            name = item.get("name", "Item")
            display = item.get("display") or f"~{_fmt_portions(item.get('portions_wasted', 0))} portions"
            lines.append(f"• {name} — {display} wasted")
    else:
        lines.append("No food or drink items detected.")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🍱 Total portions wasted: ~{_fmt_portions(total_portions)} ({emoji} {level.capitalize()})",
    ]

    if tip:
        lines += ["", f"💡 Tip: {tip}"]

    return "\n".join(lines)


async def handle_food_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    photo = update.message.photo[-1]

    await update.message.reply_text("📸 Got it! Analysing your leftovers...")

    try:
        file = await context.bot.get_file(photo.file_id)
        buf = bytearray()
        await file.download_as_bytearray(buf)
        image_bytes = bytes(buf)
    except Exception as e:
        logging.error("Failed to download photo: %s", e)
        await update.message.reply_text("⚠️ Couldn't download your photo. Please try sending it again.")
        return

    # Resize large photos before sending to AI — reduces payload and speeds up the call
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) > 1200:
            img.thumbnail((1200, 1200), Image.LANCZOS)
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=85, optimize=True)
        image_bytes = out.getvalue()
    except Exception:
        pass  # if compression fails, proceed with original

    raw_json = None
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = None
        for attempt in range(3):
            try:
                response = await openrouter_client.chat.completions.create(
                    model="google/gemini-2.5-flash",
                    max_tokens=2048,
                    response_format={"type": "json_object"},
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                            {"type": "text", "text": ANALYSIS_PROMPT},
                        ]
                    }],
                )
                break
            except Exception as retry_err:
                if attempt < 2:
                    await asyncio.sleep(5)
                else:
                    raise retry_err

        raw_json = response.choices[0].message.content.strip()
        logging.info("AI raw response (%d chars): %s", len(raw_json), raw_json[:800])

        # Strip markdown code fences robustly
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.IGNORECASE)
        raw_json = re.sub(r"\s*```\s*$", "", raw_json, flags=re.DOTALL)
        raw_json = raw_json.strip()

        # Extract JSON object if extra text surrounds it
        json_match = re.search(r"\{[\s\S]*\}", raw_json)
        if json_match:
            raw_json = json_match.group(0)

        parsed = json.loads(raw_json)

        await update.message.reply_text(
            build_report_message(parsed),
            parse_mode="Markdown",
        )

    except json.JSONDecodeError as e:
        logging.error("Gemini returned invalid JSON: %s | raw: %s", e, raw_json or "N/A")
        await update.message.reply_text("⚠️ AI returned an unexpected response. Please try again.")
    except Exception as e:
        logging.error("Food waste analysis failed: %s", e)
        await update.message.reply_text(f"⚠️ Something went wrong:\n{e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hi! I'm *SaveNoms*, your food waste tracker!\n\n"
        "After you finish a meal, send me a photo of your plate and I'll estimate how much "
        "food was left uneaten — broken down by item — plus a tip to help you right-size "
        "your portions next time. 🍽️\n\n"
        "Just snap a pic and send it over!",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💡 *How SaveNoms works*\n\n"
        "📸 Send a photo of your plate *after* eating — whatever's left uneaten.\n"
        "🔍 I'll identify each food item and estimate what percentage of it went to waste.\n"
        "📊 You'll get an overall waste score plus one actionable tip for next time.\n\n"
        "No typing needed — just send a photo whenever you're done eating.",
        parse_mode="Markdown",
    )


async def prompt_for_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "📸 Send me a photo of your leftover plate and I'll analyse the food waste for you!"
    )


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Introduce SaveNoms"),
        BotCommand("help", "How SaveNoms works"),
    ])
    await app.bot.set_my_description(
        "SaveNoms helps you cut food waste. After a meal, send a photo of your plate or "
        "drinks and I'll estimate how much was left uneaten — in exact portions or counts, "
        "not vague percentages — plus one specific tip to help you order the right amount "
        "next time. No typing needed, just snap and send. 🍽️"
    )
    await app.bot.set_my_short_description(
        "📸 Send a photo of your leftovers and I'll estimate the food waste + give you a portioning tip."
    )


def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_food_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_for_photo))

    return app
