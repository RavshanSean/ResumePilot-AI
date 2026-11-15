import { expect, test } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import App, { CandidateCard } from './App';
import { attachOriginals } from './screening';

test('starts empty with two manual categories, no sidebar, and disabled analysis', () => {
  const html = renderToStaticMarkup(<App/>);
  expect(html).toContain('Review First');
  expect(html).toContain('Needs Review');
  expect(html).toContain('Ollama matching is not connected');
  expect(html).toMatch(/<button[^>]*class="analyze"[^>]*disabled/);
  expect(html).not.toContain('<aside');
  expect(html).not.toContain('Alex Morgan');
  expect(html).toContain('multiple=""');
  expect(html).toContain('webkitdirectory=""');
});

test('both categories retain evidence and manual review controls; text is escaped', () => {
  const [candidate] = attachOriginals([{id:'0', name:'Resume', filename:'resume.pdf', summary:'Extracted', evidence:'<script>alert(1)</script>'}], [new File(['resume'], 'resume.pdf')]);
  for (const category of ['Review First', 'Needs Review'] as const) {
    const html = renderToStaticMarkup(<CandidateCard candidate={{...candidate, category}} onChange={() => {}}/>);
    expect(html).toContain('Inspect resume &amp; evidence');
    expect(html).toContain('Manual decision');
    expect(html).toContain('Review priority');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
  }
});
