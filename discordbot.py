import uuid
from itertools import chain
import discord
from discord.ext import commands
from discord.http import Route
from tortoise import Tortoise
from discord import Message, User, PartialEmoji

#################################################################
prefix = "!"
#################################################################


from utils import (
    dump_data,
    make_buttons,
    parse_components,
    parse_data,
    parse_db_data,
    parse_msg,
    progress_bar,
)

from typing import Union

import base64
import gzip
import json
import uuid


def list_chunk(lst, n):
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def dump_data(data):
    step1 = json.dumps(data, separators=(",", ":"))
    step2 = gzip.compress(step1.encode())
    step3 = base64.b85encode(step2).decode()
    return step3


def make_buttons(elements, data):
    if len(elements) * 100 - 10 < len(data):
        raise ValueError

    splited_elements = list_chunk(elements, 5)
    splited_data = list_chunk("PSTA_" + data + "_PEND", 100)

    splited_data.reverse()
    components = []

    for i in splited_elements:
        buttons = []

        for j in i:
            try:
                custom_id = splited_data.pop()
            except IndexError:
                custom_id = uuid.uuid4().hex

            if isinstance(j, PartialEmoji):
                buttons.append(
                    {
                        "type": 2,
                        "style": 1,
                        "custom_id": custom_id,
                        "emoji": j.to_dict(),
                    }
                )
            else:
                buttons.append(
                    {
                        "type": 2,
                        "style": 1,
                        "custom_id": custom_id,
                        "label": j,
                    }
                )

        components.append({"type": 1, "components": buttons})
    return components


def parse_components(raw_components):
    components = []

    index = 0
    for row in raw_components:
        for component in row["components"]:
            emoji = None
            label = None

            if component.get("emoji"):
                emoji = PartialEmoji.from_dict(component["emoji"])

            if component.get("label"):
                label = component["label"]

            components.append(
                {
                    "index": index,
                    "label": label,
                    "emoji": emoji,
                    "id": component["custom_id"],
                }
            )

            index += 1

    return components


def parse_msg(data, state):
    channel = state._get_guild_channel(data)
    message = Message(channel=channel, data=data["message"], state=state)

    if data.get("user"):
        user = User(state=state, data=data["user"])
    else:
        user = User(state=state, data=data["member"]["user"])

    custom_id = data["data"]["custom_id"]
    components = parse_components(data["message"]["components"])

    interaction_id = data["id"]
    interaction_token = data["token"]

    return message, user, custom_id, components, interaction_id, interaction_token


def parse_data(components):
    step1 = "".join(map(lambda x: x["id"], components))
    index = step1.find("_PEND")

    if not step1.startswith("PSTA_") or index == -1:
        return None

    step2 = step1[5:index]

    if step2 == ":POLL_DB:":
        return "DB"

    step3 = base64.b85decode(step2)
    step4 = gzip.decompress(step3)
    step5 = json.loads(step4)
    return step5


def parse_db_data(data):
    step1 = base64.b85decode(data)
    step2 = gzip.decompress(step1)
    step3 = json.loads(step2)
    return step3


def progress_bar(count, total):
    if total == 0:
        per = 0
    else:
        per = round(count / total * 100, 2)

    bar = "█" * int(per * 0.2)
    bar += " " * (20 - len(bar))
    return f"`{bar}` | {per}% ({count})"

class Poll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = bot._connection
        self.cache = {}

    @commands.command("종료")
    @commands.is_owner()
    async def exit_bot(self, ctx):
        await ctx.send("봇을 종료합니다.")
        await Tortoise.close_connections()
        await self.bot.close()

    @commands.command("poll", aliases=["투표"])
    async def poll(self, ctx, title=None, *elements: Union[discord.PartialEmoji, str]):
        if not title:
            return await ctx.reply("제목을 입력해주세요.")

        if not elements:
            return await ctx.reply("항목을 입력해주세요.")

        if len(elements) > 25:
            return await ctx.reply("한 번에 25개 미만의 항목을 입력해주세요.")

        if any([len(str(el)) > 50 for el in elements]):
            return await ctx.reply("항목의 길이는 50자 이하로 입력해주세요.")

        embed = discord.Embed(
            title=title,
            description="총 `0`명 투표",
            color=0x58D68D,
        )

        for element in elements:
            embed.add_field(name=element, value=progress_bar(0, 0), inline=False)

        if ctx.message.attachments:
            if ctx.message.attachments[0].url.endswith(
                (".jpg", ".jpeg", ".png", ".gif")
            ):
                embed.set_image(url=ctx.message.attachments[0].url)

        await self.bot.http.request(
            Route("POST", "/channels/{channel_id}/messages", channel_id=ctx.channel.id),
            json={
                "embed": embed.to_dict(),
                "components": make_buttons(
                    elements, dump_data([[] for _ in range(len(elements))])
                ),
            },
        )

    @commands.command("open", aliases=["개표"])
    async def open(self, ctx):
        if not ctx.message.reference:
            return await ctx.send("개표할 투표 메시지의 답장으로 이 커맨드를 사용해주세요.")

        message = await self.bot.http.request(
            Route(
                "GET",
                "/channels/{channel_id}/messages/{message_id}",
                channel_id=ctx.channel.id,
                message_id=ctx.message.reference.message_id,
            ),
        )

        components = parse_components(message["components"])

        data = parse_data(components)

        if data == "DB":
            poll_data = await PollData.filter(
                id=message["embeds"][0]["footer"]["text"]
            ).first()
            data = parse_db_data(poll_data.data)
        elif not data:
            return await ctx.send("이 메시지는 투표 메시지가 아닌 것 같아요.")

        embed = discord.Embed(title=message["embeds"][0]["title"], color=0x58D68D)

        not_polled = []

        for element in components:
            users = data[element["index"]]
            usernames = []

            for i in users:
                if self.cache.get(i):
                    usernames.append(self.cache[i])
                else:
                    user = await self.bot.fetch_user(i)
                    self.cache[i] = str(user)
                    usernames.append(str(user))

            if usernames:
                embed.add_field(
                    name=f"{element['label']} :: {len(users)}명",
                    value="\n".join(usernames),
                )
            else:
                not_polled.append(element["label"])

        if not_polled:
            embed.add_field(name="아무도 투표하지 않은 항목", value="\n".join(not_polled))

        await ctx.send(embed=embed)


    @commands.command("help", aliases=["명령어"])
    async def open(self, ctx):
        if not ctx.message.reference:
            return await ctx.send(f'[밤이 투표봇] \n !명령어 \n !투표 , !poll = 사용법 [명령어] [제목] [투표] [투표] \n !open , !개표 = 개표할 투표 메시지의 답장')




    @commands.Cog.listener()
    async def on_socket_response(self, msg):
        if msg["t"] != "INTERACTION_CREATE":
            return

        (
            message,
            user,
            custom_id,
            components,
            interaction_id,
            interaction_token,
        ) = parse_msg(msg["d"], self.state)

        self.cache[user.id] = str(user)

        poll_id = None
        data = parse_data(components)

        if data == "DB":
            poll_data = await PollData.filter(id=message.embeds[0].footer.text).first()
            poll_id = poll_data.id
            data = parse_db_data(poll_data.data)
        elif not data:
            return

        choose = list(filter(lambda x: x["id"] == custom_id, components))[0]

        if user.id in data[choose["index"]]:
            content = "투표를 취소했습니다!"
            data[choose["index"]].remove(user.id)
        elif user.id in list(chain.from_iterable(data)):
            index = list(filter(lambda x: user.id in x[1], enumerate(data)))[0][0]
            data[index].remove(user.id)
            data[choose["index"]].append(user.id)

            old_label = (
                components[index]["label"]
                if components[index]["label"]
                else str(components[index]["emoji"])
            )
            new_label = choose["label"] if choose["label"] else str(choose["emoji"])
            content = f"{old_label}에서 {new_label}(으)로 투표했습니다!"
        else:
            label = choose["label"] if choose["label"] else str(choose["emoji"])
            content = f"{label}에 투표했습니다!"
            data[choose["index"]].append(user.id)

        elements = list(
            map(lambda x: x["label"] if x["label"] else x["emoji"], components)
        )

        total = len(list(chain.from_iterable(data)))
        dumped = dump_data(data)

        embed = message.embeds[0]
        embed.description = f"총 `{total}`명 투표"

        embed.clear_fields()

        for el in components:
            embed.add_field(
                name=el["label"] if el["label"] else str(el["emoji"]),
                value=progress_bar(len(data[el["index"]]), total),
                inline=False,
            )

        if not poll_id and len(elements) * 100 - 10 < len(dumped):
            poll_id = str(uuid.uuid4())
            embed.set_footer(text=poll_id)
            await PollData.create(id=poll_id, data=dumped)

        if poll_id:
            await PollData.filter(id=poll_id).update(data=dumped)
            dumped = ":POLL_DB:"

        await self.bot.http.request(
            Route(
                "PATCH",
                "/channels/{channel_id}/messages/{message_id}",
                channel_id=message.channel[0].id,
                message_id=message.id,
            ),
            json={
                "embed": embed.to_dict(),
                "components": make_buttons(elements, dumped),
            },
        )

        await self.bot.http.request(
            Route(
                "POST",
                "/interactions/{id}/{token}/callback",
                id=interaction_id,
                token=interaction_token,
            ),
            json={
                "type": 4,
                "data": {
                    "content": content,
                    "flags": 64,
                },
            },
        )


def setup(bot):
    bot.add_cog(Poll(bot))

bot = commands.Bot(command_prefix=prefix)
bot.remove_command("help")
bot.load_extension("poll")



bot.run("MTEwMzY1NDgzMjYyMDk2MTc5Mg.GxgYJQ.osE7gEow13qnEuZW-OOkOeDFDeRiNSx_TFzYh4")
