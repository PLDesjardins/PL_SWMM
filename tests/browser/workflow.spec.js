const {test, expect} = require('@playwright/test');
const path = require('path');

test.beforeEach(async ({page}) => {
  // Exercise provider selection and layer requests without consuming map-service
  // traffic or depending on the cloud test machine's Internet allowlist.
  await page.route('https://server.arcgisonline.com/**', route => route.fulfill({contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8ioAAAAASUVORK5CYII=', 'base64')}));
});

test('import, real simulation, results, GIS and persistent reload', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#engine-version')).toHaveText('5.2.4');
  await page.locator('#open-import').click();
  await page.locator('#model-file').setInputFiles(path.resolve('examples/riverside.inp'));
  await page.locator('#model-crs').fill('EPSG:4326');
  await page.locator('#submit-import').click();
  await expect(page.locator('#import-dialog')).not.toBeVisible();
  await expect(page.locator('#model-heading')).toHaveText('riverside');
  await expect(page.locator('.leaflet-interactive')).toHaveCount(12);
  await expect(page.locator('#layer-basemap')).toHaveValue('satellite');
  await expect(page.locator('.leaflet-control-attribution')).toContainText('Esri');
  await page.locator('#run-button').click();
  await expect(page.locator('#run-status')).toHaveText('Completed', {timeout: 20000});
  await expect(page.locator('#stat-flow')).toContainText('CMS');
  await page.locator('[data-tab="results"]').click();
  await expect(page.locator('#chart svg')).toBeVisible();
  await expect(page.locator('#period-count')).toContainText('72 reporting periods');
  await page.locator('#result-kind').selectOption('nodes');
  await page.locator('#result-element').selectOption('J2');
  await expect(page.locator('#chart-title')).toContainText('J2 · Water depth');
  const csvPromise = page.waitForEvent('download');
  await page.locator('#export-csv').click();
  expect((await csvPromise).suggestedFilename()).toBe('series.csv');
  await page.locator('[data-tab="report"]').click();
  await expect(page.locator('#report-text')).toContainText('Build 5.2.4');
  await page.locator('[data-tab="network"]').click();
  await page.locator('#map-theme').selectOption('flow');
  await page.locator('#layer-subcatchments').uncheck();
  await expect(page.locator('.leaflet-interactive')).toHaveCount(9);
  await page.locator('#gis-file').setInputFiles({name: 'boundary.geojson', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({type: 'FeatureCollection', features: [{type:'Feature', properties: {}, geometry: {type: 'Point', coordinates: [-73.57, 45.506]}}]}))});
  await expect(page.locator('.overlay-row')).toContainText('boundary.geojson');
  const gisPromise = page.waitForEvent('download');
  await page.locator('#export-gis').click();
  expect((await gisPromise).suggestedFilename()).toBe('network.geojson');
  await page.reload();
  await expect(page.locator('#run-status')).toHaveText('Completed');
  await expect(page.locator('#model-heading')).toHaveText('riverside');
  expect(errors).toEqual([]);
  await page.screenshot({path: '/tmp/pl-swmm-workspace.png', fullPage: true});
});

test('PCSWMM companion CRS, missing rainfall, and attaching a new revision', async ({page}) => {
  const fs = require('fs');
  let text = fs.readFileSync('examples/riverside.inp', 'utf8');
  const rain = text.split('\n').filter(l => l.startsWith('Storm ')).map(l => l.slice(6)).join('\n');
  text = text.split('\n').filter(l => !l.startsWith('Storm ')).join('\n').replace('[TIMESERIES]', '[TIMESERIES]\nStorm FILE "C:\\PCSWMM\\rain.dat"');
  await page.goto('/');
  await page.locator('#open-import').click();
  await page.locator('#model-file').setInputFiles({name: 'pcswmm.inp', mimeType: 'text/plain', buffer: Buffer.from(text)});
  await page.locator('#companion-files').setInputFiles({name: 'pcswmm.chi', mimeType: 'text/plain', buffer: Buffer.from('[General]\nMap EPSG=4326')});
  await page.locator('#submit-import').click();
  await expect(page.locator('#import-dialog')).not.toBeVisible();
  await expect(page.locator('#model-notices')).toContainText('rain.dat');
  await expect(page.locator('#crs-label')).toContainText('EPSG:4326');
  await expect(page.locator('#run-button')).toBeDisabled();
  await page.locator('#attach-files').setInputFiles({name: 'rain.dat', mimeType: 'text/plain', buffer: Buffer.from(rain)});
  await expect(page.locator('#model-notices')).not.toContainText('Attach these');
  await expect(page.locator('#run-button')).toBeEnabled();
  await page.locator('#run-button').click();
  await expect(page.locator('#run-status')).toHaveText('Completed', {timeout: 20000});
  await page.locator('#layer-basemap').selectOption('');
  await expect(page.locator('.leaflet-control-attribution')).not.toContainText('Esri');
});

test('malformed engine input exposes report and failure state', async ({page}) => {
  await page.goto('/');
  await page.locator('#open-import').click();
  const fs = require('fs');
  const text = fs.readFileSync('examples/riverside.inp', 'utf8').replace('CIRCULAR 0.6', 'CIRCULAR -2');
  await page.locator('#model-file').setInputFiles({name: 'invalid.inp', mimeType: 'text/plain', buffer: Buffer.from(text)});
  await page.locator('#submit-import').click();
  await expect(page.locator('#import-dialog')).not.toBeVisible();
  await page.locator('#run-button').click();
  await expect(page.locator('#run-status')).toHaveText('Failed', {timeout: 20000});
  await page.locator('[data-tab="report"]').click();
  await expect(page.locator('#report-text')).toContainText('ERROR');
  await expect(page.locator('#run-button')).toBeEnabled();
});
