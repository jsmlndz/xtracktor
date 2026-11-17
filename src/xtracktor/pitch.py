"""Pitch measurement: estimate a WAV's fundamental by autocorrelation and
convert it to a MIDI note, to check stored root notes against the audio.

Percussion and noise have no definite pitch; they come back with low
confidence and should be ignored rather than trusted.
"""

import array
import math
import os
import struct
from itertools import islice
from operator import mul

from .wav import FMT, check_riff, walk_chunks

try:
    import numpy as _np
except ImportError:
    _np = None

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WINDOW = 4096
MIDI_LOW, MIDI_HIGH = 21, 108          # A0 .. C8, the piano range and a bit


def have_numpy():
    return _np is not None


def note_name(midi):
    """MIDI number -> name, with C4 = 60 (the common DAW convention)."""
    midi = int(round(midi))
    return "%s%d" % (NOTE_NAMES[midi % 12], midi // 12 - 1)


def midi_from_hz(hz):
    return 69.0 + 12.0 * math.log(hz / 440.0, 2) if hz > 0 else None


def hz_from_midi(midi):
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def analysis_window(frames):
    """(first frame, frame count) analysed: past the attack transient for a
    long sample, the whole sample for a short one."""
    if frames < WINDOW * 2:
        return 0, frames
    return frames // 4, WINDOW


def read_analysis_chunk(path):
    """Read only the stretch of a WAV that detect_pitch analyses, as mono
    16-bit samples. Returns (samples, rate, smpl root or None)."""
    with open(path, "rb") as handle:
        check_riff(handle)
        file_size = os.fstat(handle.fileno()).st_size
        rate = channels = bits = 0
        data = None
        root = None
        for cid, at, size in walk_chunks(handle):
            present = max(0, min(size, file_size - at))
            if cid == b"fmt " and present >= 16:
                handle.seek(at)
                _t, channels, rate, _br, _al, bits = FMT.unpack(
                    handle.read(16))
            elif cid == b"data":
                data = (at, present)
            elif cid == b"smpl" and present >= 36:
                handle.seek(at)
                root = struct.unpack("<9I", handle.read(36))[3]
        if data is None or not rate or bits != 16:
            raise ValueError("need 16-bit PCM with a data chunk (bits=%s)"
                             % bits)
        frame_bytes = 2 * max(channels, 1)
        start, count = analysis_window(data[1] // frame_bytes)
        handle.seek(data[0] + start * frame_bytes)
        raw = handle.read(count * frame_bytes)

    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:                            # downmix to mono
        mono = array.array("h", [0]) * (len(samples) // channels)
        for index in range(len(mono)):
            total = 0
            base = index * channels
            for offset in range(channels):
                total += samples[base + offset]
            mono[index] = int(total / channels)
        samples = mono
    return samples, rate, root


def detect_pitch(chunk, rate):
    """Autocorrelation pitch estimate of `chunk` (from read_analysis_chunk).
    Returns (hz, confidence 0..1)."""
    if len(chunk) < 256:
        return None, 0.0
    mean = sum(chunk) / float(len(chunk))
    lag_max = min(len(chunk) - 2, int(rate / hz_from_midi(MIDI_LOW)))
    lag_min = max(2, int(rate / hz_from_midi(MIDI_HIGH)))
    if lag_max <= lag_min + 2:
        return None, 0.0

    if _np is not None:
        signal = _np.asarray(chunk, dtype=_np.float64) - mean
        energy = float(_np.dot(signal, signal))
        if energy <= 0:
            return None, 0.0
        correlations = [0.0] * (lag_max + 1)
        for lag in range(1, lag_max + 1):
            correlations[lag] = float(
                _np.dot(signal[:len(signal) - lag], signal[lag:]))
    else:
        signal = [value - mean for value in chunk]
        energy = sum(value * value for value in signal)
        if energy <= 0:
            return None, 0.0
        correlations = [0.0] * (lag_max + 1)
        for lag in range(1, lag_max + 1):
            correlations[lag] = sum(map(mul, signal,
                                        islice(signal, lag, None)))

    # Autocorrelation always peaks hardest at lag 0 and stays high for small
    # lags, which makes a naive argmax report a wild harmonic. Walk past that
    # first lobe - to the first zero crossing, or failing that the first local
    # minimum - and only then look for the true period.
    start = 1
    while start < lag_max and correlations[start] > 0:
        start += 1
    if start >= lag_max:
        start = 1
        while (start + 1 < lag_max
               and correlations[start + 1] < correlations[start]):
            start += 1
    start = max(start, lag_min)
    if start >= lag_max:
        return None, 0.0

    best = start
    for lag in range(start, lag_max):
        if correlations[lag] > correlations[best]:
            best = lag
    peak = correlations[best] / energy
    lag = float(best)
    # Parabolic interpolation sharpens the lag estimate between samples.
    left = correlations[best - 1] if best > 0 else 0.0
    centre = correlations[best]
    right = correlations[best + 1] if best + 1 <= lag_max else 0.0
    denominator = (left - 2 * centre + right)
    if denominator:
        lag += 0.5 * (left - right) / denominator
    if lag <= 0:
        return None, 0.0
    return rate / lag, max(0.0, min(1.0, peak))
