import { describe, it, expect } from 'vitest';
import { searchFiles, buildDirectoryTree, flattenTree } from '../file-browser';
import type { FileDiff } from '@/types';

function makeFile (path: string): FileDiff {
  return {
    path,
    category:        'local_only',
    local_size:      100,
    remote_size:     null,
    local_mod_time:  '2024-01-01T00:00:00Z',
    remote_mod_time: null,
    is_conflict:     false,
    manual_flag:     false,
  };
}

describe('searchFiles', () => {
  const files = [
    makeFile('docs/readme.md'),
    makeFile('src/index.ts'),
    makeFile('src/utils/helpers.ts'),
  ];

  it('returns all when query is empty', () => {
    expect(searchFiles(files, '')).toHaveLength(3);
  });

  it('filters by substring case-insensitive', () => {
    expect(searchFiles(files, 'SRC')).toHaveLength(2);
  });

  it('matches partial path', () => {
    expect(searchFiles(files, 'helpers')).toHaveLength(1);
  });

  it('returns empty for no match', () => {
    expect(searchFiles(files, 'zzz')).toHaveLength(0);
  });
});

describe('buildDirectoryTree', () => {
  it('builds correct tree structure', () => {
    const files = [
      makeFile('src/a.ts'),
      makeFile('src/b.ts'),
      makeFile('docs/readme.md'),
      makeFile('root.txt'),
    ];
    const tree = buildDirectoryTree(files);

    // Root should have 2 child dirs and 1 root file
    expect(tree.children).toHaveLength(2);
    expect(tree.files).toHaveLength(1);
    expect(tree.files[0].path).toBe('root.txt');

    const srcNode = tree.children.find(c => c.name === 'src');
    expect(srcNode).toBeDefined();
    expect(srcNode!.files).toHaveLength(2);

    const docsNode = tree.children.find(c => c.name === 'docs');
    expect(docsNode).toBeDefined();
    expect(docsNode!.files).toHaveLength(1);
  });

  it('handles nested directories', () => {
    const files = [makeFile('a/b/c/file.txt')];
    const tree = buildDirectoryTree(files);
    expect(tree.children).toHaveLength(1);
    expect(tree.children[0].name).toBe('a');
    expect(tree.children[0].children[0].name).toBe('b');
    expect(tree.children[0].children[0].children[0].name).toBe('c');
    expect(tree.children[0].children[0].children[0].files).toHaveLength(1);
  });

  it('preserves total file count', () => {
    const files = Array.from({ length: 50 }, (_, i) => makeFile(`dir${i % 5}/file${i}.txt`));
    const tree = buildDirectoryTree(files);
    // Count all files in tree recursively
    function countFiles (node: typeof tree): number {
      return node.files.length + node.children.reduce((s, c) => s + countFiles(c), 0);
    }
    expect(countFiles(tree)).toBe(50);
  });
});

describe('flattenTree', () => {
  const files = [
    makeFile('src/a.ts'),
    makeFile('src/b.ts'),
    makeFile('docs/readme.md'),
  ];
  const tree = buildDirectoryTree(files);

  it('shows only top-level dirs when nothing expanded', () => {
    const rows = flattenTree(tree, new Set());
    expect(rows).toHaveLength(2); // docs, src dirs
    expect(rows.every(r => r.type === 'dir')).toBe(true);
  });

  it('shows child files when dir expanded', () => {
    const rows = flattenTree(tree, new Set(['src']));
    // docs dir (collapsed) + src dir (expanded) + 2 src files
    expect(rows).toHaveLength(4);
    const fileRows = rows.filter(r => r.type === 'file');
    expect(fileRows).toHaveLength(2);
  });

  it('tracks depth correctly', () => {
    const deepFiles = [makeFile('a/b/deep.txt')];
    const deepTree = buildDirectoryTree(deepFiles);
    const rows = flattenTree(deepTree, new Set(['a', 'a/b']));
    // dir a (depth 0), dir b (depth 1), file deep.txt (depth 2)
    expect(rows).toHaveLength(3);
    expect(rows[0].depth).toBe(0);
    expect(rows[1].depth).toBe(1);
    expect(rows[2].depth).toBe(2);
  });
});
