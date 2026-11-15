import { expect, test } from 'vitest';
import { fileError } from './files';
test('accepts supported resumes, rejects invalid uploads', () => {
 expect(fileError({name:'Resume.PDF',size:123})).toBeNull();
 expect(fileError({name:'Resume.docx',size:123})).toBeNull();
 expect(fileError({name:'script.exe',size:123})).toBeTruthy();
 expect(fileError({name:'empty.pdf',size:0})).toBeTruthy();
 expect(fileError({name:'large.pdf',size:11*1024*1024})).toBeTruthy();
});
