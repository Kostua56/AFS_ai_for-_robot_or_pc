#!/usr/bin/env python3
import pygame
import sys
import time
import random
import threading
import requests
import subprocess
import os
import json
import re
import math
from datetime import datetime

# === ПОПЫТКА ИМПОРТА VOSK ===
try:
    from vosk import Model, KaldiRecognizer
    import pyaudio
    VOSK_AVAILABLE = True
except ImportError:
    print("⚠️  Vosk or PyAudio not installed. Install them:")
    print("   pip install vosk pyaudio")
    print("   sudo apt install portaudio19-dev")
    VOSK_AVAILABLE = False

# === ИНИЦИАЛИЗАЦИЯ ===
pygame.init()
WIDTH, HEIGHT = 1000, 700
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Cute Robot Kitty <3")
clock = pygame.time.Clock()

# === ЦВЕТА ===
BACKGROUND = (0, 0, 0)
TEXT_COLOR = (255, 255, 255)
LISTENING_COLOR = (100, 200, 255)
BLUSH_COLOR = (255, 150, 150)
IDLE_COLOR = (150, 150, 150)
ROBOT_COLOR = (255, 180, 255)

# === ШРИФТЫ ===
face_font = pygame.font.SysFont("monospace", 140, bold=True)
status_font = pygame.font.SysFont("dejavusans", 36, bold=True)

# === ЛИЦА ===
IDLE_FACE = "(•ω•)"
BLINK_FACE = "(^ω^)"
SPEAKING_FACE = "(^▽^)"
LISTENING_FACE = "(•◡•)"
BLUSH_FACE = "(\\>\▽\<\\)"

# === КЛЮЧЕВЫЕ СЛОВА ДЛЯ ПОКРАСНЕНИЯ ===
COMPLIMENT_WORDS = [
    "милый", "красивый", "умный", "люблю", "обожаю", "хороший",
    "прекрасный", "замечательный", "чудесный", "симпатичный",
    "котик", "котёнок", "кошечка", "роботик",
    "cute", "beautiful", "smart", "love", "adore", "good",
    "wonderful", "amazing", "fantastic", "sweet",
    "kitty", "kitten", "robot"
]

# === НАСТРОЙКИ ===
PIPER_PATH = "/home/user/piper/piper"
VOICE_MODEL = "/home/user/piper/voices/en_US/amy/medium/en_US-amy-medium.onnx"
VOICE_CONFIG = "/home/user/piper/voices/en_US/amy/medium/en_US-amy-medium.onnx.json"
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "gemma3:27b-cloud"
VOSK_MODEL_PATH = "/home/kostik/vosk-en/vosk-model-small-en-us-0.15"

# === ПАМЯТЬ РОБОТА ===
conversation_memory = [
    {"role": "system", "content": "You are a cute catgirl robot named Mika. Always respond in English, be friendly, playful, and remember our conversation. Use emojis like ^▽^ and •ω• to express emotions!"}
]
MAX_MEMORY_MESSAGES = 12

# === СОСТОЯНИЯ ===
is_blinking = False
blink_start = 0
blink_duration = 0.15
last_blink = time.time()
is_speaking = False
mic_active = False
wake_word_active = True
stop_listening = False
blush_until = 0
audio_stream = None
pyaudio_instance = None
wake_stream = None
wake_pyaudio = None
speech_start_time = 0
mic_was_active_before_speaking = False

# === УТИЛИТЫ ДЛЯ ТЕРМИНАЛА ===
def timestamp():
    """Возвращает текущее время в формате [HH:MM:SS]"""
    return datetime.now().strftime("[%H:%M:%S]")

def print_user(text):
    """Вывод сообщения пользователя в терминал с цветом"""
    print(f"{timestamp()} \033[1;32m[ты]\033[0m {text}")

def print_robot(text):
    """Вывод сообщения робота в терминал с цветом"""
    print(f"{timestamp()} \033[1;35m[мика]\033[0m {text}")

def print_system(text, color="36"):
    """Вывод системных сообщений"""
    print(f"{timestamp()} \033[1;{color}m[система]\033[0m {text}")

# === ПРОВЕРКА КОМПЛИМЕНТОВ ===
def is_compliment(text):
    text_lower = text.lower()
    for word in COMPLIMENT_WORDS:
        if word in text_lower:
            return True
    return False

# === TTS (PIPER) С ЛОГИРОВАНИЕМ В ТЕРМИНАЛ ===
def speak_text(text):
    global is_speaking, blush_until, speech_start_time, mic_active, stop_listening, mic_was_active_before_speaking
    
    # Логируем ответ робота В ТЕРМИНАЛ
    print_robot(text)
    
    mic_was_active_before_speaking = mic_active
    
    if mic_active:
        stop_listening = True
        mic_active = False
        print_system("микрофон отключён (робот говорит)", "33")
    
    if is_compliment(text):
        blush_until = time.time() + 2.5
    
    is_speaking = True
    speech_start_time = time.time()
    
    def tts_thread():
        try:
            clean_text = text.replace("*", "").replace("#", "").replace("```", "").strip()
            if not clean_text:
                clean_text = "hi"
            
            subprocess.run(
                [
                    PIPER_PATH,
                    "--model", VOICE_MODEL,
                    "--config", VOICE_CONFIG,
                    "--output_file", "/tmp/robot_response.wav"
                ],
                input=clean_text.encode('utf-8'),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            subprocess.run(["aplay", "-q", "/tmp/robot_response.wav"], check=False)
        except Exception as e:
            print_system(f"ошибка TTS: {e}", "31")
        finally:
            global is_speaking, mic_active, stop_listening
            is_speaking = False
            
            if mic_was_active_before_speaking and not stop_listening:
                mic_active = True
                stop_listening = False
                threading.Thread(target=listen_main_microphone, daemon=True).start()
                print_system("микрофон включён (робот закончил говорить)", "32")
    
    threading.Thread(target=tts_thread, daemon=True).start()

# === OLLAMA С ПАМЯТЬЮ ===
def ask_ollama(prompt):
    global conversation_memory
    
    try:
        conversation_memory.append({"role": "user", "content": prompt})
        
        if len(conversation_memory) > MAX_MEMORY_MESSAGES + 1:
            conversation_memory = [conversation_memory[0]] + conversation_memory[-MAX_MEMORY_MESSAGES:]
        
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": conversation_memory,
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            answer = response.json()["message"]["content"].strip()
            conversation_memory.append({"role": "assistant", "content": answer})
            
            if len(conversation_memory) > MAX_MEMORY_MESSAGES + 1:
                conversation_memory = [conversation_memory[0]] + conversation_memory[-MAX_MEMORY_MESSAGES:]
            
            return answer
        else:
            fallback = "Meow? I didn't understand..."
            conversation_memory.append({"role": "assistant", "content": fallback})
            return fallback
            
    except Exception as e:
        print_system(f"ошибка Ollama: {e}", "31")
        fallback = "Meow? My brain is fuzzy..."
        conversation_memory.append({"role": "assistant", "content": fallback})
        return fallback

# === ОСНОВНОЙ МИКРОФОН ===
def listen_main_microphone():
    global mic_active, stop_listening, audio_stream, pyaudio_instance
    
    if not VOSK_AVAILABLE or not os.path.exists(VOSK_MODEL_PATH):
        mic_active = False
        return
    
    try:
        model = Model(VOSK_MODEL_PATH)
        recognizer = KaldiRecognizer(model, 16000)
        
        pyaudio_instance = pyaudio.PyAudio()
        audio_stream = pyaudio_instance.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=8000
        )
        audio_stream.start_stream()
        
        recognizer.Reset()
        print_system("микрофон включён", "32")
        
        while mic_active and not stop_listening:
            try:
                data = audio_stream.read(4000, exception_on_overflow=False)
                
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()
                    if text and mic_active:
                        # Логируем распознанный текст В ТЕРМИНАЛ
                        print_user(text)
                        
                        if is_compliment(text):
                            global blush_until
                            blush_until = time.time() + 2.0
                            speak_text("Eeek... thank you... *blushes*")
                        
                        stop_listening = True
                        mic_active = False
                        
                        def get_response():
                            answer = ask_ollama(text)
                            speak_text(answer)
                        
                        threading.Thread(target=get_response, daemon=True).start()
                        recognizer.Reset()
                
            except Exception as e:
                print_system(f"ошибка аудио: {e}", "31")
                break
        
        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
        if pyaudio_instance:
            pyaudio_instance.terminate()
        
        print_system("микрофон выключён", "33")
        
    except Exception as e:
        print_system(f"ошибка микрофона: {e}", "31")
    finally:
        mic_active = False
        stop_listening = False

# === WAKE WORD МИКРОФОН ===
def listen_wake_word():
    global wake_word_active, wake_stream, wake_pyaudio, mic_active
    
    if not VOSK_AVAILABLE or not os.path.exists(VOSK_MODEL_PATH):
        return
    
    try:
        model = Model(VOSK_MODEL_PATH)
        recognizer = KaldiRecognizer(model, 16000)
        
        wake_pyaudio = pyaudio.PyAudio()
        wake_stream = wake_pyaudio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=8000
        )
        wake_stream.start_stream()
        
        print_system("фоновое прослушивание 'котик/kitty' запущено", "34")
        
        while wake_word_active:
            try:
                data = wake_stream.read(4000, exception_on_overflow=False)
                
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip().lower()
                    
                    if not is_speaking and ("котик" in text or "котёнок" in text or "kitty" in text or "robot" in text):
                        print_system(f"услышано ключевое слово: '{text}'", "35")
                        
                        if not mic_active:
                            mic_active = True
                            stop_listening = False
                            threading.Thread(target=listen_main_microphone, daemon=True).start()
                            
                            global blush_until
                            blush_until = time.time() + 3.0
                            speak_text("Meow! I'm here!")
                        
                        recognizer.Reset()
            
            except Exception as e:
                print_system(f"ошибка wake-word: {e}", "31")
                break
        
        if wake_stream:
            wake_stream.stop_stream()
            wake_stream.close()
        if wake_pyaudio:
            wake_pyaudio.terminate()
            
    except Exception as e:
        print_system(f"ошибка wake-word микрофона: {e}", "31")

# === ПЕРЕКЛЮЧЕНИЕ МИКРОФОНА ===
def toggle_microphone():
    global mic_active, stop_listening
    
    if is_speaking:
        print_system("нельзя переключить микрофон во время речи", "33")
        return
    
    if mic_active:
        stop_listening = True
        mic_active = False
        print_system("микрофон выключен вручную", "33")
    else:
        mic_active = True
        stop_listening = False
        threading.Thread(target=listen_main_microphone, daemon=True).start()
        print_system("микрофон включён вручную", "32")

# === СБРОС ПАМЯТИ ===
def reset_memory():
    global conversation_memory
    conversation_memory = [
        {"role": "system", "content": "You are a cute catgirl robot named Mika. Always respond in English, be friendly, playful, and remember our conversation. Use emojis like ^▽^ and •ω• to express emotions!"}
    ]
    print_system("память сброшена — новый разговор!", "36")
    speak_text("Memory reset! Let's start fresh! ^▽^")

# === ПРИВЕТСТВИЕ ===
print("\033[1;35m" + "="*60 + "\033[0m")
print_system("РОБОТ-КОТИК МИКА ЗАПУЩЕН", "35")
print_system("Голосовой ассистент с памятью и эмоциями", "35")
print_system("Управление:", "34")
print_system("  SPACE — включить/выключить микрофон", "34")
print_system("  R     — сбросить память", "34")
print_system("  ESC   — выйти", "34")
print_system("Скажи 'котик' или 'kitty' для активации!", "35")
print("\033[1;35m" + "="*60 + "\033[0m\n")

# === ЗАПУСК WAKE WORD В ФОНЕ ===
if VOSK_AVAILABLE and os.path.exists(VOSK_MODEL_PATH):
    threading.Thread(target=listen_wake_word, daemon=True).start()
else:
    print_system("⚠️  Vosk не доступен — микрофон отключён", "31")

# Приветствие
speak_text("Hello! I'm Mika, your catgirl robot friend! Say 'kitty' or press SPACE!")

# === ОСНОВНОЙ ЦИКЛ ===
running = True
while running:
    current_time = time.time()
    
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE and not is_speaking:
                toggle_microphone()
            elif event.key == pygame.K_r and not is_speaking:
                reset_memory()
            elif event.key == pygame.K_ESCAPE:
                running = False

    # Анимация мигания
    if not is_speaking and not mic_active and current_time > blush_until:
        if not is_blinking and current_time - last_blink > random.uniform(2.0, 4.0):
            is_blinking = True
            blink_start = current_time
            last_blink = current_time
        
        if is_blinking and current_time - blink_start > blink_duration:
            is_blinking = False

    # Анимация кивания при речи
    bob_offset = 0
    if is_speaking:
        bob_offset = math.sin((current_time - speech_start_time) * 1.5 * 2 * math.pi) * 10

    # Выбор лица
    if current_time < blush_until:
        face = BLUSH_FACE
        face_color = BLUSH_COLOR
    elif is_speaking:
        face = SPEAKING_FACE
        face_color = ROBOT_COLOR
    elif mic_active:
        face = LISTENING_FACE
        face_color = LISTENING_COLOR
    elif is_blinking:
        face = BLINK_FACE
        face_color = TEXT_COLOR
    else:
        face = IDLE_FACE
        face_color = TEXT_COLOR

    # Отрисовка (чистый минималистичный интерфейс!)
    screen.fill(BACKGROUND)
    
    face_text = face_font.render(face, True, face_color)
    base_y = HEIGHT // 2 - 30
    draw_y = base_y + (bob_offset if is_speaking else 0)
    screen.blit(face_text, face_text.get_rect(center=(WIDTH // 2, draw_y)))
    
    # Статус
    if is_speaking:
        status = "💬 Speaking... (mic muted)"
        status_color = ROBOT_COLOR
    elif mic_active:
        status = "🎤 Listening..."
        status_color = LISTENING_COLOR
    elif current_time < blush_until:
        status = "😳 *blushing*"
        status_color = BLUSH_COLOR
    else:
        status = "💤 Say 'kitty' or press SPACE (R to reset)"
        status_color = IDLE_COLOR
    
    status_text = status_font.render(status, True, status_color)
    screen.blit(status_text, status_text.get_rect(center=(WIDTH // 2, HEIGHT - 80)))
    
    pygame.display.flip()
    clock.tick(60)

# Очистка ресурсов
wake_word_active = False
stop_listening = True

if audio_stream:
    audio_stream.stop_stream()
    audio_stream.close()
if pyaudio_instance:
    pyaudio_instance.terminate()
if wake_stream:
    wake_stream.stop_stream()
    wake_stream.close()
if wake_pyaudio:
    wake_pyaudio.terminate()

print("\n\033[1;35m" + "="*60 + "\033[0m")
print_system("МИКА ЗАВЕРШИЛА РАБОТУ. ПОКА-ПОКА! ^ω^", "35")
print("\033[1;35m" + "="*60 + "\033[0m\n")
pygame.quit()
sys.exit()
