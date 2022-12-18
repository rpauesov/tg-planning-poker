import collections
import json

import aiosqlite

AVAILABLE_POINTS = [
    "1", "2", "3", "5", "8",
    "13", "20", "40", "❔", "☕",
]
HALF_POINTS = len(AVAILABLE_POINTS) // 2
ALL_MARKS = "♥♦♠♣"


class Vote:
    def __init__(self):
        self.point = ""
        self.version = -1

    def set(self, point):
        self.point = point
        self.version += 1

    @property
    def masked(self):
        return ALL_MARKS[self.version % len(ALL_MARKS)]

    def to_dict(self):
        return {
            "point": self.point,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, dct):
        res = cls()
        res.point = dct["point"]
        res.version = dct["version"]
        return res


class Game:
    OP_RESTART = "restart"
    OP_RESTART_NEW = "restart-new"
    OP_REVEAL = "reveal"
    OP_REVEAL_NEW = "reveal-new"

    def __init__(self, chat_id, vote_id, initiator, text):
        self.chat_id = chat_id
        self.vote_id = vote_id
        self.initiator = initiator
        self.text = text
        self.reply_message_id = 0
        self.votes = collections.defaultdict(Vote)
        self.revealed = False

    def add_vote(self, initiator, point):
        self.votes[self._initiator_str(initiator)].set(point)

    def get_text(self):
        result = "{} по задаче:\n{}\nИнициатор: {}".format(
            "Голосование" if not self.revealed else "Результаты голосования",
            self.text, self._initiator_str(self.initiator)
        )
        if self.votes:
            result += "\n\nВсего голосов: " + str(len(self.votes.items()))
            votes_str = "\n".join(
                "{:3s} {}".format(
                    vote.point if self.revealed else vote.masked, user_id
                )
                for user_id, vote in sorted(self.votes.items())
            )
            result += "\n\nТекущие оценки:\n{}".format(votes_str)
        all_num_votes = list(filter(lambda x: x.isdigit(), [vote.point for user_id, vote in self.votes.items()]))
        all_votes = sum([int(x) for x in all_num_votes])
        if len(self.votes) > 0 and self.revealed:
            result += "\n\nСредняя оценка: {}".format(all_votes / len(all_num_votes))
            result += "\n\nРаспределение по голосам:\n"
            for point in AVAILABLE_POINTS:
                point_votes = list(filter(lambda p: p == point, [vote.point for user_id, vote in self.votes.items()]))
                count = len(point_votes)
                if count > 0:
                    result += f"\n{point} - " + "🟩" * count + f" ({count})"
        return result

    def get_send_kwargs(self):
        return {"text": self.get_text(), "reply_markup": json.dumps(self.get_markup())}

    def get_markup(self):
        points_keys = [
            {
                "type": "InlineKeyboardButton",
                "text": point,
                "callback_data": "vote-click-{}-{}".format(self.vote_id, point),
            }
            for point in AVAILABLE_POINTS
        ]
        return {
            "type": "InlineKeyboardMarkup",
            "inline_keyboard": [
                points_keys[:HALF_POINTS],
                points_keys[HALF_POINTS:],
                [
                    {
                        "type": "InlineKeyboardButton",
                        "text": "Вскрываемся",
                        "callback_data": "{}-click-{}".format(self.OP_REVEAL, self.vote_id),
                    },
                    {
                        "type": "InlineKeyboardButton",
                        "text": "Рестарт",
                        "callback_data": "{}-click-{}".format(self.OP_RESTART, self.vote_id),
                    }
                ],
            ],
        }

    def restart(self):
        self.votes.clear()
        self.revealed = False

    @staticmethod
    def _initiator_str(initiator: dict) -> str:
        return "@{} ({})".format(
            initiator.get("username") or initiator.get("id"),
            initiator["first_name"]
        )

    def to_dict(self):
        return {
            "initiator": self.initiator,
            "text": self.text,
            "reply_message_id": self.reply_message_id,
            "revealed": self.revealed,
            "votes": {user_id: vote.to_dict() for user_id, vote in self.votes.items()}
        }

    @classmethod
    def from_dict(cls, chat_id, vote_id, dct):
        res = cls(chat_id, vote_id, dct["initiator"], dct["text"])
        for user_id, vote in dct["votes"].items():
            res.votes[user_id] = Vote.from_dict(vote)
        res.revealed = dct["revealed"]
        res.reply_message_id = dct["reply_message_id"]
        return res


class GameRegistry:
    def __init__(self):
        self._db = None

    async def init_db(self, db_path):
        con = aiosqlite.connect(db_path)
        con.daemon = True
        self._db = await con
        # It's pretty dumb schema, but I'm too lazy for proper normalized tables for this task
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS games (
                chat_id, game_id, 
                json_data,
                PRIMARY KEY (chat_id, game_id)
            )
        """)

    def new_game(self, chat_id, incoming_message_id: str, initiator: dict, text: str):
        return Game(chat_id, incoming_message_id, initiator, text)

    async def get_game(self, chat_id, incoming_message_id: str) -> Game:
        query = 'SELECT json_data FROM games WHERE chat_id = ? AND game_id = ?'
        async with self._db.execute(query, (chat_id, incoming_message_id)) as cursor:
            res = await cursor.fetchone()
            if not res:
                return None
            return Game.from_dict(chat_id, incoming_message_id, json.loads(res[0]))

    async def save_game(self, game: Game):
        await self._db.execute(
            "INSERT OR REPLACE INTO games VALUES (?, ?, ?)",
            (game.chat_id, game.vote_id, json.dumps(game.to_dict()))
        )
        await self._db.commit()
