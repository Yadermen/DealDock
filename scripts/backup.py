from steam_radar.config import get_settings
from steam_radar.services.backup import create_backup

if __name__ == "__main__":
    print(create_backup(get_settings()))
