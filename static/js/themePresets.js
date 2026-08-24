// Theme preset table, extracted from theme.js for the module-size
// ratchet (scripts/check_module_sizes.py). Pure data: 5 tokens per
// theme (bg, fg, panel, border, red=accent) plus optional advanced
// overrides. Helios/Solstice are Apollo's signature pair.
export const THEMES = {
  // Helios — Apollo's signature: deep night-blue ground, warm parchment
  // text, restrained solar-gold accent. Solstice is its light twin.
  helios:     { bg:'#131722', fg:'#e9e4d6', panel:'#0d111a', border:'#2b3345', red:'#e3aa4e' },
  solstice:   { bg:'#faf7f0', fg:'#33302a', panel:'#ffffff', border:'#ddd5c4', red:'#a97a24' },
  dark:       { bg:'#282c34', fg:'#9cdef2', panel:'#111111', border:'#355a66', red:'#e06c75' },
  light:      { bg:'#f0ebe3', fg:'#5a5248', panel:'#faf6f0', border:'#d4cdc2', red:'#c47d5a' },
  midnight:   { bg:'#0d1117', fg:'#c9d1d9', panel:'#161b22', border:'#30363d', red:'#f85149' },
  paper:      { bg:'#faf8f5', fg:'#3b3836', panel:'#ffffff', border:'#d5d0c8', red:'#c5ac4a' },
  // Spicy / fun themes
  cyberpunk:  { bg:'#0a0a0f', fg:'#0ff0fc', panel:'#12101a', border:'#9b30ff', red:'#e040fb' },
  retrowave:  { bg:'#1a1a2e', fg:'#e94560', panel:'#16213e', border:'#533483', red:'#e94560' },
  forest:     { bg:'#1b2a1b', fg:'#a8d5a2', panel:'#142414', border:'#3d6b3d', red:'#7cb871' },
  ocean:      { bg:'#0b1a2c', fg:'#64d2ff', panel:'#091422', border:'#1e5074', red:'#4facfe' },
  ume:        { bg:'#2b1b2e', fg:'#f5c2e7', panel:'#1e1420', border:'#6c4675', red:'#f5a0c0' },
  copper:     { bg:'#1c1410', fg:'#e8c39e', panel:'#140f0a', border:'#7a5533', red:'#d4764e' },
  terminal:   { bg:'#000000', fg:'#00ff41', panel:'#0a0a0a', border:'#003b00', red:'#00ff41' },
  organs:     { bg:'#0a0406', fg:'#efe1c8', panel:'#15080a', border:'#3a1519', red:'#c83240' },
  lavender:   { bg:'#f3eef8', fg:'#3d3551', panel:'#faf7ff', border:'#cec3de', red:'#9b6dcc' },
  gpt:        { bg:'#212121', fg:'#ececec', panel:'#171717', border:'#424242', red:'#949494',
                advanced: { sendBtnBg: '#949494', sendBtnHover: '#7f7f7f',
                            userBubbleBg: '#2f2f2f', aiBubbleBg: '#171717',
                            inputBg: '#2f2f2f' } },
  claude:     { bg:'#262624', fg:'#f5f4f0', panel:'#30302e', border:'#4a4a47', red:'#c6613f' },
  cute:       { bg:'#fff0f5', fg:'#d4608a', panel:'#fff8fa', border:'#f0c0d0', red:'#ff6b9d' },
  // Classic editor palettes
  nord:       { bg:'#2e3440', fg:'#d8dee9', panel:'#3b4252', border:'#4c566a', red:'#bf616a' },
  dracula:    { bg:'#282a36', fg:'#f8f8f2', panel:'#1e1f29', border:'#6272a4', red:'#ff5555' },
  gruvbox:    { bg:'#282828', fg:'#ebdbb2', panel:'#1d2021', border:'#665c54', red:'#fb4934' },
  rosepine:   { bg:'#191724', fg:'#e0def4', panel:'#1f1d2e', border:'#524f67', red:'#eb6f92' },
  sunset:     { bg:'#251521', fg:'#ffd9a0', panel:'#1a0e17', border:'#7d4a5a', red:'#ff8c5a' },
  // Light modes
  solarized:  { bg:'#fdf6e3', fg:'#586e75', panel:'#eee8d5', border:'#c9c0a3', red:'#cb4b16' },
  mint:       { bg:'#eef7f1', fg:'#29473b', panel:'#ffffff', border:'#b7d8c6', red:'#2f9e6b' },
  contrast:   { bg:'#ffffff', fg:'#111111', panel:'#f4f4f4', border:'#666666', red:'#b00020' },
};
