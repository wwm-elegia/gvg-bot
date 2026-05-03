import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TOKEN")
DATA_FILE = "raids.json"

MSK = timezone(timedelta(hours=3))

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


data = load_data()


TEAM_LIMITS = {
    "attack": {"tank": 2, "heal": 3, "dps": 10},
    "defense": {"tank": 2, "heal": 3, "dps": 10},
}

RESERVE_LIMITS = {
    "tank": 4,
    "heal": 4,
    "dps": 10,
}

TEAM_NAMES = {
    "attack": "Команда Атаки",
    "defense": "Команда Защиты",
}

ROLE_NAMES = {
    "tank": "🛡️ Танки",
    "heal": "🍃 Хилы",
    "dps": "⚔️ ДД",
}


def normalize_raid(raid: dict):
    if isinstance(raid.get("reserve"), list):
        old_reserve = raid["reserve"]
        raid["reserve"] = {"tank": [], "heal": [], "dps": old_reserve}

    if "reserve" not in raid:
        raid["reserve"] = {"tank": [], "heal": [], "dps": []}

    for role in ("tank", "heal", "dps"):
        raid["reserve"].setdefault(role, [])


def all_signed_ids(raid: dict) -> set[str]:
    normalize_raid(raid)

    ids = set()
    for team in ("attack", "defense"):
        for role in ("tank", "heal", "dps"):
            ids.update(raid["teams"][team][role])

    for role in ("tank", "heal", "dps"):
        ids.update(raid["reserve"][role])

    return ids


def find_user_position(raid: dict, user_id: str):
    normalize_raid(raid)

    for team in ("attack", "defense"):
        for role in ("tank", "heal", "dps"):
            if user_id in raid["teams"][team][role]:
                return ("team", team, role)

    for role in ("tank", "heal", "dps"):
        if user_id in raid["reserve"][role]:
            return ("reserve", None, role)

    return None


def remove_user_from_raid(raid: dict, user_id: str):
    pos = find_user_position(raid, user_id)
    if not pos:
        return None

    if pos[0] == "reserve":
        _, _, role = pos
        raid["reserve"][role].remove(user_id)
        return ("reserve", None, role)

    _, team, role = pos
    raid["teams"][team][role].remove(user_id)
    return ("team", team, role)


def try_move_from_reserve(raid: dict, team: str, role: str):
    normalize_raid(raid)

    if not raid["reserve"][role]:
        return None

    if len(raid["teams"][team][role]) >= TEAM_LIMITS[team][role]:
        return None

    moved_user_id = raid["reserve"][role].pop(0)
    raid["teams"][team][role].append(moved_user_id)
    return moved_user_id


def format_mentions(user_ids: list[str]) -> str:
    if not user_ids:
        return "—"
    return "\n".join(f"<@{uid}>" for uid in user_ids)


def team_total(team_data: dict) -> int:
    return sum(len(team_data[role]) for role in ("tank", "heal", "dps"))


def reserve_total(reserve_data: dict) -> int:
    return sum(len(reserve_data[role]) for role in ("tank", "heal", "dps"))


def build_raid_message(raid_id: str) -> str:
    raid = data[raid_id]
    normalize_raid(raid)

    timestamp = raid["time"]

    attack = raid["teams"]["attack"]
    defense = raid["teams"]["defense"]
    reserve = raid["reserve"]

    lines = [
        f"## {raid['title']}",
        f"🕒 **По МСК:** {raid['date_msk']} {raid['time_msk']}  **(локальное: <t:{timestamp}:F>)**",
        "",
        f"**Команда Атаки** — {team_total(attack)}/15",
        f"🛡️ Танки ({len(attack['tank'])}/2)",
        format_mentions(attack["tank"]),
        f"🍃 Хилы ({len(attack['heal'])}/3)",
        format_mentions(attack["heal"]),
        f"⚔️ ДД ({len(attack['dps'])}/10)",
        format_mentions(attack["dps"]),
        "",
        f"**Команда Защиты** — {team_total(defense)}/15",
        f"🛡️ Танки ({len(defense['tank'])}/2)",
        format_mentions(defense["tank"]),
        f"🍃 Хилы ({len(defense['heal'])}/3)",
        format_mentions(defense["heal"]),
        f"⚔️ ДД ({len(defense['dps'])}/10)",
        format_mentions(defense["dps"]),
        "",
        f"**Резерв** — {reserve_total(reserve)}/18",
        f"🛡️ Танки ({len(reserve['tank'])}/4)",
        format_mentions(reserve["tank"]),
        f"🍃 Хилы ({len(reserve['heal'])}/4)",
        format_mentions(reserve["heal"]),
        f"⚔️ ДД ({len(reserve['dps'])}/10)",
        format_mentions(reserve["dps"]),
    ]
    return "\n".join(lines)


async def refresh_raid_message(raid_id: str):
    raid = data.get(raid_id)
    if not raid:
        return

    channel = bot.get_channel(raid["channel_id"])
    if channel is None:
        return

    try:
        message = await channel.fetch_message(raid["message_id"])
    except discord.NotFound:
        return

    await message.edit(content=build_raid_message(raid_id), view=GVGView(raid_id))


class GVGView(discord.ui.View):
    def __init__(self, raid_id: str):
        super().__init__(timeout=None)
        self.raid_id = raid_id

    async def add_to_team(self, interaction: discord.Interaction, team: str, role: str):
        raid = data.get(self.raid_id)
        if not raid:
            await interaction.response.send_message("Эта запись уже не существует.", ephemeral=True)
            return

        normalize_raid(raid)

        user_id = str(interaction.user.id)

        if user_id in all_signed_ids(raid):
            await interaction.response.send_message("Ты уже записан. Сначала отписывайся.", ephemeral=True)
            return

        current = raid["teams"][team][role]
        limit = TEAM_LIMITS[team][role]

        if len(current) >= limit:
            await interaction.response.send_message("Этот слот уже заполнен.", ephemeral=True)
            return

        current.append(user_id)
        save_data(data)
        await refresh_raid_message(self.raid_id)

        await interaction.response.send_message(
            f"Ты записан: {TEAM_NAMES[team]} / {ROLE_NAMES[role]}",
            ephemeral=True
        )

    async def add_to_reserve(self, interaction: discord.Interaction, role: str):
        raid = data.get(self.raid_id)
        if not raid:
            await interaction.response.send_message("Эта запись уже не существует.", ephemeral=True)
            return

        normalize_raid(raid)

        user_id = str(interaction.user.id)

        if user_id in all_signed_ids(raid):
            await interaction.response.send_message("Ты уже записан. Сначала отписывайся.", ephemeral=True)
            return

        if len(raid["reserve"][role]) >= RESERVE_LIMITS[role]:
            await interaction.response.send_message("Резерв на эту роль уже заполнен.", ephemeral=True)
            return

        raid["reserve"][role].append(user_id)
        save_data(data)
        await refresh_raid_message(self.raid_id)

        await interaction.response.send_message(
            f"Ты записан в резерв: {ROLE_NAMES[role]}",
            ephemeral=True
        )

    @discord.ui.button(label="Атака 🛡️", style=discord.ButtonStyle.primary, row=0, custom_id="attack_tank")
    async def attack_tank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "attack", "tank")

    @discord.ui.button(label="Атака 🍃", style=discord.ButtonStyle.success, row=0, custom_id="attack_heal")
    async def attack_heal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "attack", "heal")

    @discord.ui.button(label="Атака ⚔️", style=discord.ButtonStyle.secondary, row=0, custom_id="attack_dps")
    async def attack_dps(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "attack", "dps")

    @discord.ui.button(label="Защита 🛡️", style=discord.ButtonStyle.primary, row=1, custom_id="defense_tank")
    async def defense_tank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "defense", "tank")

    @discord.ui.button(label="Защита 🍃", style=discord.ButtonStyle.success, row=1, custom_id="defense_heal")
    async def defense_heal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "defense", "heal")

    @discord.ui.button(label="Защита ⚔️", style=discord.ButtonStyle.secondary, row=1, custom_id="defense_dps")
    async def defense_dps(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_team(interaction, "defense", "dps")

    @discord.ui.button(label="Резерв 🛡️", style=discord.ButtonStyle.primary, row=2, custom_id="reserve_tank")
    async def reserve_tank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_reserve(interaction, "tank")

    @discord.ui.button(label="Резерв 🍃", style=discord.ButtonStyle.success, row=2, custom_id="reserve_heal")
    async def reserve_heal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_reserve(interaction, "heal")

    @discord.ui.button(label="Резерв ⚔️", style=discord.ButtonStyle.secondary, row=2, custom_id="reserve_dps")
    async def reserve_dps(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.add_to_reserve(interaction, "dps")

    @discord.ui.button(label="❌ Отписаться", style=discord.ButtonStyle.danger, row=3, custom_id="leave_btn")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        raid = data.get(self.raid_id)
        if not raid:
            await interaction.response.send_message("Эта запись уже не существует.", ephemeral=True)
            return

        normalize_raid(raid)

        user_id = str(interaction.user.id)
        removed_pos = remove_user_from_raid(raid, user_id)

        if not removed_pos:
            await interaction.response.send_message("Ты не записан.", ephemeral=True)
            return

        moved_user_id = None
        moved_team = None
        moved_role = None

        if removed_pos[0] == "team":
            _, team, role = removed_pos
            moved_user_id = try_move_from_reserve(raid, team, role)
            moved_team = team
            moved_role = role

        save_data(data)
        await refresh_raid_message(self.raid_id)

        if moved_user_id:
            await interaction.response.send_message(
                f"Ты отписался.\nИз резерва автоматически добавлен <@{moved_user_id}> "
                f"в {TEAM_NAMES[moved_team]} / {ROLE_NAMES[moved_role]}.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Ты отписался.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")

    for raid_id in data.keys():
        bot.add_view(GVGView(raid_id))

    if not reminder_loop.is_running():
        reminder_loop.start()

    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано команд: {len(synced)}")
    except Exception as e:
        print(f"Ошибка sync команд: {e}")


@bot.tree.command(name="gvg_create", description="Создать запись на ГВГ")
async def gvg_create(interaction: discord.Interaction, title: str, date: str, time_msk: str):
    await interaction.response.defer(ephemeral=True)

    try:
        dt_msk = datetime.strptime(f"{date} {time_msk}", "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
    except ValueError:
        await interaction.followup.send(
            "Неверный формат. Дата: **дд.мм.гггг**, время: **чч:мм**",
            ephemeral=True
        )
        return

    timestamp = int(dt_msk.timestamp())
    raid_id = str(int(max(data.keys(), default="0")) + 1)

    data[raid_id] = {
        "title": title,
        "date_msk": date,
        "time_msk": time_msk,
        "time": timestamp,
        "channel_id": interaction.channel.id,
        "message_id": None,
        "thread_id": None,
        "teams": {
            "attack": {"tank": [], "heal": [], "dps": []},
            "defense": {"tank": [], "heal": [], "dps": []},
        },
        "reserve": {
            "tank": [],
            "heal": [],
            "dps": [],
        },
        "reminders_sent": {
            "3600": False,
            "600": False,
            "0": False
        }
    }

    save_data(data)

    view = GVGView(raid_id)
    message = await interaction.channel.send(build_raid_message(raid_id), view=view)
    thread = await message.create_thread(name=f"Обсуждение {title}")

    data[raid_id]["message_id"] = message.id
    data[raid_id]["thread_id"] = thread.id
    save_data(data)

    bot.add_view(GVGView(raid_id))

    await interaction.followup.send("Запись создана ✅", ephemeral=True)


@tasks.loop(minutes=1)
async def reminder_loop():
    now_ts = int(datetime.now(timezone.utc).timestamp())

    for raid_id, raid in data.items():
        normalize_raid(raid)
        raid_time = raid["time"]

        reminders = [
            (3600, "ГВГ через 1 час"),
            (600, "ГВГ через 10 минут"),
            (0, "ГВГ началось"),
        ]

        all_ids = sorted(all_signed_ids(raid))
        if not all_ids:
            continue

        mentions = " ".join(f"<@{uid}>" for uid in all_ids)
        channel = bot.get_channel(raid["channel_id"])
        if channel is None:
            continue

        for seconds_before, text in reminders:
            key = str(seconds_before)
            if raid["reminders_sent"].get(key):
                continue

            if now_ts >= raid_time - seconds_before:
                try:
                    await channel.send(f"{mentions}\n**{raid['title']}** — {text}")
                    raid["reminders_sent"][key] = True
                    save_data(data)
                except Exception as e:
                    print(f"Ошибка напоминания {raid_id}: {e}")


bot.run(TOKEN)
