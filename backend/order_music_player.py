import os
import time
import pygame
from django.db import connection
from django.conf import settings

from backend.models import Order

# Ensure Django settings are configured
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()



# Path to your music file
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC_FILE = os.path.join(BASE_DIR, "backend", "music", "test.mp3")

# Initialize pygame mixer
pygame.mixer.init()
pygame.mixer.music.load(MUSIC_FILE)
pygame.mixer.music.play(-1)  # Play in a loop but initially paused
pygame.mixer.music.pause()


def monitor_order_seen_status():
    """
    Continuously monitor the 'seen' field of the latest Order and control song playback.
    """
    song_playing = False  # Track the current playback state

    while True:
        try:
            # Fetch the latest order
            latest_order = Order.objects.order_by('-created_at').first()
            if latest_order:
                if not latest_order.seen and not song_playing:
                    pygame.mixer.music.unpause()  # Play the song
                    print("Song is playing")  # Print status
                    song_playing = True
                elif latest_order.seen and song_playing:
                    pygame.mixer.music.pause()  # Pause the song
                    print("Song is NOT playing")  # Print status
                    song_playing = False
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(2)  # Check every 2 seconds


if __name__ == "__main__":
    monitor_order_seen_status()
