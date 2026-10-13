"""Generate locally authorized and Fernet-encrypted MTProto session string.

Run only in a secure terminal with Telethon installed. Never commit the output.
"""
import asyncio
import getpass
from cryptography.fernet import Fernet
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id=int(input('Telegram API ID: '))
    api_hash=getpass.getpass('Telegram API hash: ')
    key=getpass.getpass('Existing ENCRYPTION_KEY (Fernet key): ')
    session=StringSession()
    client=TelegramClient(session,api_id,api_hash)
    await client.start()
    serialized=client.session.save()
    print('\nTELEGRAM_SESSION_ENCRYPTED='+Fernet(key.encode()).encrypt(serialized.encode()).decode())
    print('Set the encrypted value in .env; it authorizes your account. Keep it secret.')
    await client.disconnect()


if __name__=='__main__':asyncio.run(main())
