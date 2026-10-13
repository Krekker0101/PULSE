"""Persistent aiogram FSM: no Redis DNS dependency, survives bot and machine restarts."""
import asyncio
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from aiogram.fsm.state import State
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models import DialogState


def key_id(key: StorageKey) -> str:
    return '|'.join(str(x or 0) for x in (
        key.bot_id, key.chat_id, key.user_id, key.thread_id,
        getattr(key,'business_connection_id',None) or '', key.destiny))


_UNSET=object()


class PostgresFSMStorage(BaseStorage):
    async def close(self) -> None:
        pass

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await asyncio.to_thread(self._save, key_id(key), state=value)

    async def get_state(self, key: StorageKey) -> str | None:
        return await asyncio.to_thread(self._load, key_id(key), 'state')

    async def set_data(self, key: StorageKey, data: dict) -> None:
        await asyncio.to_thread(self._save, key_id(key), data=dict(data))

    async def get_data(self, key: StorageKey) -> dict:
        return dict(await asyncio.to_thread(self._load, key_id(key), 'data') or {})

    @staticmethod
    def _load(identity: str, field: str):
        with SessionLocal() as db:
            item=db.get(DialogState, identity)
            return getattr(item, field) if item else None

    @staticmethod
    def _save(identity: str, *, state=_UNSET, data=_UNSET):
        # Missing kwargs are identified via sentinel below in the public calls.
        with SessionLocal() as db:
            record=db.get(DialogState, identity)
            if record is None:
                record=DialogState(identity=identity,state=None,data={})
                db.add(record)
            if state is not _UNSET: record.state=state
            if data is not _UNSET: record.data=data
            db.commit()

