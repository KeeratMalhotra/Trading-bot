// Tiny synthesized UI sounds (no audio files). Browsers need a user click before audio plays.
let ctx: AudioContext | null = null;

function ac(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const C = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!C) return null;
    ctx = new C();
  }
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function tone(freq: number, start: number, dur: number, type: OscillatorType = "sine", gain = 0.06) {
  const c = ac();
  if (!c) return;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type;
  o.frequency.value = freq;
  g.gain.setValueAtTime(0, c.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, c.currentTime + start + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + start + dur);
  o.connect(g).connect(c.destination);
  o.start(c.currentTime + start);
  o.stop(c.currentTime + start + dur + 0.05);
}

export const sounds = {
  open() {
    tone(660, 0, 0.12, "triangle");
    tone(880, 0.08, 0.18, "triangle");
  },
  win() {
    tone(523, 0, 0.12, "triangle");
    tone(659, 0.09, 0.12, "triangle");
    tone(784, 0.18, 0.28, "triangle");
  },
  loss() {
    tone(392, 0, 0.18, "sine");
    tone(311, 0.14, 0.3, "sine");
  },
  alert() {
    tone(220, 0, 0.25, "sawtooth", 0.04);
    tone(220, 0.3, 0.25, "sawtooth", 0.04);
  },
  unlock() {
    ac();
  },
};
