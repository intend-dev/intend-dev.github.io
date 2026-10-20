import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// One list of candidate browsers for every script that drives Chromium, shared with
// scripts/check-browser.py through chrome.json. CHROME_PATH wins so a CI image or a
// portable install can point at its own binary.
const here = dirname(fileURLToPath(import.meta.url));
const paths = JSON.parse(readFileSync(join(here, 'chrome.json'), 'utf8'));

const candidates = () => {
  const list = [];
  if (process.env.CHROME_PATH) list.push(process.env.CHROME_PATH);
  if (process.platform === 'win32') {
    for (const root of paths.windowsRoots.map((name) => process.env[name]).filter(Boolean)) {
      for (const relative of paths.win32) list.push(join(root, relative));
    }
  } else if (process.platform === 'darwin') {
    list.push(...paths.darwin);
  } else {
    for (const name of paths.linux) {
      const found = spawnSync('which', [name], { encoding: 'utf8' });
      if (found.status === 0 && found.stdout.trim()) list.push(found.stdout.trim());
    }
  }
  return list;
};

export const findChrome = () => candidates().find((candidate) => existsSync(candidate));
