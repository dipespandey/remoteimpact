import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';

const output = resolve('../static');
const copy = (source, target) => {
  const dest = resolve(output, target);
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(resolve('node_modules', source), dest);
};

for (const [pkg, source] of [['alpinejs', 'dist/cdn.min.js'], ['htmx.org', 'dist/htmx.min.js']]) {
  const metadata = JSON.parse(readFileSync(resolve('node_modules', pkg, 'package.json')));
  const name = pkg === 'alpinejs' ? 'alpine' : 'htmx';
  copy(`${pkg}/${source}`, `vendor/${name}-${metadata.version}.min.js`);
}
copy('htmx.org/LICENSE', 'vendor/htmx-LICENSE.txt');
copy('@vue/reactivity/LICENSE', 'vendor/vue-reactivity-LICENSE.txt');

let fonts = '';
for (const [pkg, family] of [['geist', 'Geist'], ['geist-mono', 'Geist Mono']]) {
  const root = `@fontsource-variable/${pkg}`;
  let css = readFileSync(resolve('node_modules', root, 'index.css'), 'utf8');
  for (const match of css.matchAll(/url\(\.\/files\/([^)]*)\)/g)) {
    copy(`${root}/files/${match[1]}`, `fonts/${match[1]}`);
  }
  copy(`${root}/LICENSE`, `fonts/${pkg}-LICENSE.txt`);
  css = css.replaceAll(`'${family} Variable'`, `'${family}'`).replaceAll('url(./files/', 'url(fonts/');
  fonts += css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ');
}
writeFileSync(resolve('generated-fonts.css'), fonts + '\n');
