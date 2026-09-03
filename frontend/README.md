# Token Watcher

I need you to build a complete frontend UI from scratch for an existing project called Rug Pull Detector.

I cannot upload my current frontend, so do not assume you can inspect or modify existing files. Generate the frontend as a self-contained implementation that I can later integrate into my React/Vite project.

PROJECT

Rug Pull Detector is a real-time blockchain security tool that detects newly deployed ERC-20 tokens and analyzes them for potential rug pulls.

The backend already exists. Do not build a backend. Do not create mock blockchain logic. Focus entirely on the frontend UI.

Assume the API provides token objects shaped roughly like:

{
  contract_address: string;
  deployer: string;
  name: string;
  symbol: string;
  decimals: number;
  total_supply: string;
  creation_block: number;
  creation_timestamp: string;
  detected_at: string;
  risk?: {
    score: number;
    level: string;
    reasons: string[];
    computed_at: string;
  };
}

DESIGN VISION

Create something dark, fun, chaotic, weird, green, hacker-like, and memorable.

It should feel like a custom underground blockchain intelligence tool, somewhere between:

 a hacker terminal

 malware analysis software

 retro computer interfaces

 cyber surveillance software

 experimental web art

Do NOT make a generic SaaS dashboard.

STRICTLY AVOID

 Glassmorphism

 Purple gradients

 Generic blue cyberpunk UI

 Floating gradient blobs

 Oversized rounded cards

 Generic crypto startup aesthetic

 Excessive dashboards full of meaningless statistics

VISUAL STYLE

 Near-black background

 Matrix/hacker green as the primary accent

 Off-white and muted grey text

 Monospace fonts for addresses and technical information

 Bold experimental typography for headings

 Subtle scanlines, CRT texture, noise, grids, or terminal artifacts

 Sharp borders and unusual layout elements

Make it feel handcrafted and slightly chaotic, while keeping important information readable.

INTERACTIVE MOVABLE TITLES

Important titles should not be static.

Create draggable title elements that users can move around the interface.

The main title:

RUG PULL DETECTOR

should behave like an interactive draggable object with smooth movement and slight rotation/tilt.

Add several smaller movable labels around the interface, but keep them controlled so they don't interfere with usability.

Use browser-native pointer events or lightweight React logic instead of adding unnecessary heavy dependencies.

CREATOR SIGNATURE

Add:

MADE BY JARVIS

somewhere visible and stylish.

It should feel like a signature hidden inside a piece of hacker software rather than a normal footer.

Possible styling:

 Small floating draggable label

 Terminal signature

 Slight glitch animation

 Fixed corner signature

Make it memorable.

MAIN LIVE FEED

The main page should be a real-time LIVE TOKEN FEED.

Tokens should appear like incoming security events, not ecommerce cards.

Each token row should display:

 Token symbol

 Token name

 Shortened contract address

 Detection timestamp

 Risk score

 Risk level

Example:

[RUG]
RUGPULL COIN
0x333333...3333

RISK // 95
CRITICAL

Risk levels:

 Critical: red accent

 Suspicious: amber/yellow accent

 Low: green accent

Green should remain the overall interface accent, while risk colours are reserved for risk status.

MICROINTERACTIONS

Add interesting interactions such as:

 Token rows animate when entering the feed

 Hover effects feel like inspecting a system record

 Subtle glitch effects

 Blinking terminal cursor

 LIVE indicator pulse

 Draggable headings

 Slight text distortion on hover

Do not make everything move constantly. Motion should feel deliberate.

TOP SYSTEM BAR

Include a compact system/status area containing things like:

SYSTEM: ONLINE
NETWORK: BASE
SCANNER: ACTIVE

Make it look like part of a surveillance/analysis tool.

TECHNICAL REQUIREMENTS

Build this using:

 React

 TypeScript

 CSS

Keep components reasonably modular.

Generate:

 Main application layout

 Live Feed page

 Token row component

 Risk score display component

 Draggable title component

 Relevant CSS/styles

Do not build backend functionality.

Use mock data only to demonstrate the UI, but structure components so the mock data can later be replaced with API data matching the provided object structure.

The result must feel distinctive and handmade. It should look like a weird, cool blockchain surveillance tool someone actually designed, not a template generated from “make me a futuristic hacker dashboard.”

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/ef4d0ce7-3a05-4488-8886-dc928210f9f3).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
