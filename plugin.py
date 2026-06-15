import logging
import os
import re
import time
import requests
from app.plugin_api import PixooPluginBase

logger = logging.getLogger(__name__)

class AniWorldPlugin(PixooPluginBase):
    def setup(self):
        self.base_url = os.getenv("BASE_URL", "http://jellyfin:8080")
        self.user = os.getenv("USER", "admin")
        self.password = os.getenv("PASSWORD", "sfYg452pmZ*KWGuqVDUJ")
        self.update_interval = int(os.getenv("UPDATE_INTERVAL", 3))
        
        self.session = requests.Session()
        
        self.last_drawn_state = None
        self.real_download_confirmed = False
        self.current_episode_id = None
        self.episode_streak = 0
        self.REQUIRED_STREAK = 3
        
        # Simulated Pixoo for rendering
        self.pixoo = self.get_pixoo_instance()
        
    def perform_login(self):
        try:
            response = self.session.get(f"{self.base_url}/login", timeout=5)
            token_match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
            if not token_match: return False

            csrf_token = token_match.group(1)
            login_data = {"csrf_token": csrf_token, "username": self.user, "password": self.password}
            res = self.session.post(f"{self.base_url}/login", data=login_data, timeout=5)
            return res.status_code == 200 or "dashboard" in res.url
        except Exception as e:
            logger.error(f"Login fehlgeschlagen: {e}")
            return False

    def get_downloader_data(self):
        try:
            response = self.session.get(f"{self.base_url}/api/queue", timeout=5)
            if response.status_code == 401:
                if self.perform_login():
                    response = self.session.get(f"{self.base_url}/api/queue", timeout=5)
                else: return None
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Downloader-Daten: {e}")
            return None

    def format_episode_string(self, active_item):
        url = active_item.get('current_url', '')
        s_match = re.search(r'staffel-(\d+)', url)
        e_match = re.search(r'episode-(\d+)', url)
        if s_match and e_match:
            s = s_match.group(1).zfill(2)
            e = e_match.group(1).zfill(3)
            return f"S{s}E{e}"
        curr_ep = str(active_item.get('current_episode', 0)).zfill(3)
        return f"Episode {curr_ep}"

    def get_text_width(self, text):
        return len(text) * 4

    def wrap_text(self, text, max_width=60):
        words = text.split(' ')
        lines = ["", ""]
        while words and self.get_text_width((lines[0] + " " + words[0]).strip()) <= max_width:
            lines[0] = (lines[0] + " " + words.pop(0)).strip()
        if not lines[0] and words:
            chars = max_width // 4
            lines[0] = words[0][:chars]
            words[0] = words[0][chars:]
        if not words: return [lines[0]]
        while words and self.get_text_width((lines[1] + " " + words[0]).strip()) <= max_width:
            lines[1] = (lines[1] + " " + words.pop(0)).strip()
        if words:
            if not lines[1]: lines[1] = words[0]
            while self.get_text_width(lines[1] + "...") > max_width and len(lines[1]) > 0:
                lines[1] = lines[1][:-1]
            lines[1] = (lines[1].strip() + "...")
        return [l for l in lines if l]

    def update_display(self, data):
        if not data or 'items' not in data:
            self.last_drawn_state = None
            return False

        active_item = next((item for item in data['items'] if item['status'] == "running"), None)
        if active_item:
            title = active_item.get('title', 'Download')
            title_lines = self.wrap_text(title, 60)
            ep_code = self.format_episode_string(active_item)
            prog_info = f"Ep {active_item.get('current_episode')}/{active_item.get('total_episodes')}"
            
            ffmpeg_data = data.get('ffmpeg_progress', {})
            percent = float(ffmpeg_data.get('percent', 0.0))
            bandwidth = ffmpeg_data.get('bandwidth', '0.0 MB/s')

            current_state = (title, ep_code, int(percent), bandwidth)
            if self.last_drawn_state == current_state:
                return True

            self.last_drawn_state = current_state
            
            self.pixoo.fill((0, 0, 0))
            self.pixoo.draw_text(title_lines[0], (2, 2), (255, 180, 0))
            if len(title_lines) > 1:
                self.pixoo.draw_text(title_lines[1], (2, 10), (255, 180, 0))

            self.pixoo.draw_text(ep_code, (2, 22), (0, 255, 255))
            self.pixoo.draw_text(prog_info, (2, 34), (150, 150, 150))
            self.pixoo.draw_text(f"{int(percent)}%", (2, 46), (255, 255, 255))

            bw_x = 62 - (len(bandwidth) * 4)
            self.pixoo.draw_text(bandwidth, (bw_x, 46), (255, 255, 255))

            for y in range(54, 57):
                self.pixoo.draw_line((2, y), (61, y), (40, 40, 40))

            bar_width = int((percent / 100) * 59)
            if bar_width > 0:
                for y in range(54, 57):
                    self.pixoo.draw_line((2, y), (2 + bar_width, y), (0, 255, 100))

            self.pixoo.push()
            return True
        else:
            self.last_drawn_state = None
            return False

    def loop(self):
        while True:
            try:
                data = self.get_downloader_data()
                active_item = None
                if data and 'items' in data:
                    active_item = next((item for item in data['items'] if item['status'] == "running"), None)

                if active_item:
                    title = active_item.get('title', 'Download')
                    ep_code = self.format_episode_string(active_item)
                    item_id = f"{title}_{ep_code}"

                    if self.current_episode_id != item_id:
                        self.current_episode_id = item_id
                        self.episode_streak = 1
                    else:
                        self.episode_streak += 1

                    if self.episode_streak >= self.REQUIRED_STREAK:
                        self.real_download_confirmed = True

                    if self.real_download_confirmed:
                        self.update_display(data)
                else:
                    self.current_episode_id = None
                    self.episode_streak = 0
                    if self.real_download_confirmed:
                        self.release_screen()
                        self.real_download_confirmed = False
                        self.update_display(None)
            except Exception as e:
                logger.error(f"Plugin error: {e}")
                
            time.sleep(self.update_interval)
