# -*- coding: utf-8 -*-
"""
Generate 6 crisp, high-definition 44.1kHz 16-bit PCM WAV notification sounds
Using only Python standard library (wave, math, struct)
"""
import os
import wave
import math
import struct

SAMPLE_RATE = 44100

def create_wav(filename, samples):
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)        # Mono
        wf.setsampwidth(2)        # 16-bit PCM
        wf.setframerate(SAMPLE_RATE)
        raw = bytearray()
        for s in samples:
            val = max(-32767, min(32767, int(s * 32767)))
            raw.extend(struct.pack('<h', val))
        wf.writeframes(raw)

def envelope_decay(t, total_t, attack=0.01):
    if t < attack:
        return t / attack
    return math.exp(-3.5 * (t - attack) / (total_t - attack))

def gen_5m_entry(path):
    # 🎯 5m买点: 两个上扬清脆音 (659.25Hz -> 880Hz)
    duration = 0.30
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    t_mid = 0.12
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        if t < t_mid:
            freq = 659.25
            amp = 0.65 * envelope_decay(t, t_mid, 0.008)
            s = amp * (math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t))
        else:
            t_rel = t - t_mid
            freq = 880.0
            amp = 0.85 * envelope_decay(t_rel, duration - t_mid, 0.008)
            s = amp * (math.sin(2 * math.pi * freq * t_rel) + 0.3 * math.sin(4 * math.pi * freq * t_rel) + 0.1 * math.sin(6 * math.pi * freq * t_rel))
        samples.append(s)
    create_wav(path, samples)

def gen_risk_alarm(path):
    # 💼 持仓风控: 双声紧急警报 (987.77Hz, 783.99Hz)
    duration = 0.38
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    p1 = 0.14
    gap = 0.04
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        if t < p1:
            freq = 987.77
            amp = 0.85 * envelope_decay(t, p1, 0.006)
            s = amp * (0.8 * math.sin(2 * math.pi * freq * t) + 0.2 * math.sin(6 * math.pi * freq * t))
        elif t < p1 + gap:
            s = 0.0
        else:
            t_rel = t - (p1 + gap)
            freq = 783.99
            amp = 0.90 * envelope_decay(t_rel, duration - (p1 + gap), 0.006)
            s = amp * (0.8 * math.sin(2 * math.pi * freq * t_rel) + 0.25 * math.sin(6 * math.pi * freq * t_rel))
        samples.append(s)
    create_wav(path, samples)

def gen_golden_pit(path):
    # 🟢 黄金坑就位: 舒缓温润水滴 / 水晶双音 (523.25Hz, 659.25Hz)
    duration = 0.42
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        amp = 0.75 * math.exp(-4.2 * t / duration)
        s1 = math.sin(2 * math.pi * 523.25 * t)
        s2 = 0.7 * math.sin(2 * math.pi * 659.25 * t)
        s3 = 0.3 * math.sin(2 * math.pi * 1046.50 * t)
        samples.append(amp * (s1 + s2 + s3) / 2.0)
    create_wav(path, samples)

def gen_breakdown(path):
    # 🔴 破位预警: 低沉下坠音 (400Hz -> 180Hz)
    duration = 0.35
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    phase = 0.0
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        progress = t / duration
        freq = 400.0 - 220.0 * (progress ** 0.8)
        phase += 2 * math.pi * freq / SAMPLE_RATE
        amp = 0.85 * (1.0 - progress ** 1.2)
        s = amp * (0.85 * math.sin(phase) + 0.35 * math.sin(2 * phase) + 0.15 * math.sin(3 * phase))
        samples.append(s)
    create_wav(path, samples)

def gen_take_profit(path):
    # 🟣 止盈冲高: 金币/收银机 4连音快速上扬 (587Hz -> 740Hz -> 880Hz -> 1175Hz)
    duration = 0.42
    notes = [587.33, 739.99, 880.00, 1174.66]
    note_dur = duration / len(notes)
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        idx = min(len(notes) - 1, int(t / note_dur))
        t_note = t - idx * note_dur
        freq = notes[idx]
        amp = 0.80 * math.exp(-3.8 * t_note / note_dur)
        s = amp * (math.sin(2 * math.pi * freq * t_note) + 0.35 * math.sin(3 * math.pi * freq * t_note))
        samples.append(s)
    create_wav(path, samples)

def gen_macro_weather(path):
    # ⛅ 市场气象: 柔和马林巴双音 (440Hz -> 554.37Hz)
    duration = 0.40
    total_samples = int(SAMPLE_RATE * duration)
    samples = []
    t_mid = 0.15
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        if t < t_mid:
            freq = 440.0
            amp = 0.70 * envelope_decay(t, t_mid, 0.01)
            s = amp * math.sin(2 * math.pi * freq * t)
        else:
            t_rel = t - t_mid
            freq = 554.37
            amp = 0.75 * envelope_decay(t_rel, duration - t_mid, 0.01)
            s = amp * math.sin(2 * math.pi * freq * t_rel)
        samples.append(s)
    create_wav(path, samples)

def main():
    sounds_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
    os.makedirs(sounds_dir, exist_ok=True)
    
    gen_5m_entry(os.path.join(sounds_dir, "5m_entry.wav"))
    gen_risk_alarm(os.path.join(sounds_dir, "risk_alarm.wav"))
    gen_golden_pit(os.path.join(sounds_dir, "golden_pit.wav"))
    gen_breakdown(os.path.join(sounds_dir, "breakdown.wav"))
    gen_take_profit(os.path.join(sounds_dir, "take_profit.wav"))
    gen_macro_weather(os.path.join(sounds_dir, "weather.wav"))
    print(f"OK: Generated 6 WAV sound files in {sounds_dir}")

if __name__ == "__main__":
    main()
