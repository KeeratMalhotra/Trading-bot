// Bot registry: names/colors come from the server, with static fallbacks.
export interface BotInfo {
  name: string;
  color: string;
  short: string;
}

const registry: Record<string, BotInfo> = {
  oracle: { name: "ORACLE", color: "#a78bfa", short: "O" },
  nomad: { name: "NOMAD", color: "#38bdf8", short: "N" },
  low: { name: "SENTINEL", color: "#34d399", short: "S" },
  medium: { name: "TACTICIAN", color: "#fbbf24", short: "T" },
  high: { name: "BERSERKER", color: "#f43f5e", short: "B" },
  hodl: { name: "HODL", color: "#94a3b8", short: "H" },
  system: { name: "ARENA", color: "#94a3b8", short: "A" },
};

export function updateRegistry(bots: { id: string; name: string; color: string }[]) {
  for (const b of bots) {
    const cur = registry[b.id];
    registry[b.id] = { name: b.name, color: b.color, short: cur?.short ?? b.name[0] };
  }
}

export const botInfo = (id: string): BotInfo => registry[id] ?? { name: id.toUpperCase(), color: "#94a3b8", short: id[0]?.toUpperCase() ?? "?" };
export const botName = (id: string) => botInfo(id).name;
export const botColor = (id: string) => botInfo(id).color;
