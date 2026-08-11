import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const lock = JSON.parse(await readFile(path.join(workspace, 'package-lock.json'), 'utf8'));
const packages = [];

for (const location of Object.keys(lock.packages)) {
	if (!location.startsWith('node_modules/')) continue;

	const manifestPath = path.join(workspace, location, 'package.json');
	try {
		const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
		packages.push({
			name: manifest.name,
			version: manifest.version,
			license: manifest.license ?? 'UNKNOWN',
			installed: true
		});
	} catch (error) {
		if (error?.code !== 'ENOENT') throw error;
		const locked = lock.packages[location];
		packages.push({
			name: location.slice(location.lastIndexOf('node_modules/') + 13),
			version: locked.version,
			license: 'NOT_INSTALLED_OPTIONAL',
			installed: false
		});
	}
}

packages.sort((left, right) => left.name.localeCompare(right.name));
process.stdout.write(`${JSON.stringify(packages, null, 2)}\n`);
