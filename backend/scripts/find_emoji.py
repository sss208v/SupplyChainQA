import re, glob, os
emoji_re = re.compile('[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FAFF\U00002702-\U000027B0\u26A0\uFE0F]')
base = r'C:\Users\sss208\Desktop\agent\supply-chain-qa\frontend\src'
for f in glob.glob(os.path.join(base, '**', '*.*'), recursive=True):
    if f.endswith(('.vue', '.js')):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                for i, line in enumerate(fh, 1):
                    if emoji_re.search(line):
                        rel = os.path.relpath(f, base)
                        print(f'{rel}:{i}: {line.rstrip()[:100]}')
        except: pass
