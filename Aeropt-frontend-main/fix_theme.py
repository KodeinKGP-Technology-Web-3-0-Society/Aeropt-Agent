import re
import glob

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    # Fix 1: Remove duplicate dark: classes where pattern is "dark:text-slate-X dark:text-slate-Y text-slate-Z"
    # The auto-conversion script created nonsensical duplicate dark: classes
    # Pattern: dark:text-slate-400 dark:text-slate-600 text-slate-400 -> dark:text-slate-400 text-slate-600
    content = re.sub(r'dark:text-slate-400 dark:text-slate-600 text-slate-400', 'dark:text-slate-400 text-slate-500', content)
    
    # Fix text-slate-500 text-slate-500 duplicates
    content = re.sub(r'dark:text-slate-500 text-slate-500 dark:text-slate-500 text-slate-500', 'dark:text-slate-500 text-slate-500', content)
    content = re.sub(r'dark:text-slate-500 text-slate-500(?= dark:text-slate-500)', 'dark:text-slate-500 text-slate-500', content)
    
    # Fix: hover:dark:bg -> dark:hover:bg (proper Tailwind v4 ordering)
    content = re.sub(r'hover:dark:bg-', 'dark:hover:bg-', content)
    content = re.sub(r'hover:dark:text-', 'dark:hover:text-', content)
    content = re.sub(r'hover:dark:border-', 'dark:hover:border-', content)
    
    # Fix: group-hover:dark: -> dark:group-hover:
    content = re.sub(r'group-hover:dark:', 'dark:group-hover:', content)
    
    # Fix: selection:dark:text-white text-slate-900 -> selection:text-white
    content = re.sub(r'selection:dark:text-white text-slate-900', 'selection:text-white', content)
    
    # Fix 2: Buttons with bg-[#6258ff] should always have text-white, not text-slate-900
    # Pattern: bg-[#6258ff]...dark:text-white text-slate-900 -> bg-[#6258ff]...text-white
    # We need a more targeted approach. Replace "dark:text-white text-slate-900" that comes after bg-[#6258ff] or bg-[#5045ff]
    # Actually let's just fix buttons explicitly:
    content = content.replace('bg-[#6258ff] hover:bg-[#5045ff] dark:text-white text-slate-900', 'bg-[#6258ff] hover:bg-[#5045ff] text-white')
    content = content.replace('bg-[#6258ff] hover:bg-[#5045ff] active:scale-[0.99] dark:text-white text-slate-900', 'bg-[#6258ff] hover:bg-[#5045ff] active:scale-[0.99] text-white')
    content = content.replace('bg-[#6258ff] hover:bg-[#5045ff] active:scale-[0.98] dark:text-white text-slate-900', 'bg-[#6258ff] hover:bg-[#5045ff] active:scale-[0.98] text-white')
    content = content.replace('bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-50 disabled:cursor-not-allowed dark:text-white text-slate-900', 'bg-[#6258ff] hover:bg-[#5045ff] disabled:opacity-50 disabled:cursor-not-allowed text-white')
    
    # Fix the "Start Session" button and similar
    content = content.replace('active:scale-[0.98] dark:text-white text-slate-900 font-semibold', 'active:scale-[0.98] text-white font-semibold')
    content = content.replace('active:scale-[0.99] dark:text-white text-slate-900 font-semibold', 'active:scale-[0.99] text-white font-semibold')
    
    # Fix any remaining purple-bg buttons with wrong text color
    content = re.sub(
        r'(bg-\[#6258ff\][^"]*?)dark:text-white text-slate-900',
        r'\1text-white',
        content
    )
    
    # Fix brand icon inside purple bg - always white 
    content = content.replace(
        'rounded-md bg-[#6258ff] dark:text-white text-slate-900',
        'rounded-md bg-[#6258ff] text-white'
    )
    content = content.replace(
        'rounded-lg bg-gradient-to-br from-[#776eff] to-[#5045ff] dark:text-white text-slate-900',
        'rounded-lg bg-gradient-to-br from-[#776eff] to-[#5045ff] text-white'
    )
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed: {filepath}")

files = glob.glob('src/components/**/*.tsx', recursive=True) + glob.glob('src/app/**/*.tsx', recursive=True)
for f in files:
    fix_file(f)

