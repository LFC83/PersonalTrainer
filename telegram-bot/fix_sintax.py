# Criar script de correção
#!/usr/bin/env python3
import sys

print("🔧 A corrigir erros de sintaxe no main.py...")

# Ler ficheiro
try:
    with open('main.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
except FileNotFoundError:
    print("❌ main.py não encontrado!")
    sys.exit(1)

print(f"📄 Ficheiro tem {len(lines)} linhas")

# Correção 1: Linha 34 (aproximadamente, procurar pela string)
found_line_34 = False
for i, line in enumerate(lines):
    if '- Evita caracteres especiais: $ ( ) \\' in line:
        print(f"✅ Encontrada linha problemática em {i+1}: removendo backslashes")
        lines[i] = line.replace(
            '- Evita caracteres especiais: $ ( ) \\ { } exceto em pontuação normal',
            '- Evita símbolos matemáticos especiais exceto em pontuação normal'
        )
        found_line_34 = True

if not found_line_34:
    print("⚠️  Linha 34 não encontrada ou já corrigida")

# Correção 2: Patterns (linhas 1171-1173 aproximadamente)
patterns_fixed = 0
for i, line in enumerate(lines):
    if 'CallbackQueryHandler' in line and "pattern='^" in line and "pattern=r'^" not in line:
        print(f"✅ Corrigindo pattern na linha {i+1}")
        lines[i] = line.replace("pattern='^", "pattern=r'^")
        patterns_fixed += 1

print(f"✅ {patterns_fixed} patterns corrigidos")

# Salvar
with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("✅ Ficheiro corrigido e salvo!")

# Verificação
print("\n🔍 Verificação:")
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()
    
if "pattern=r'^sync_confirmed" in content:
    print("✅ pattern=r'^sync_confirmed$' encontrado")
else:
    print("❌ pattern ainda sem r prefix")

if '- Evita símbolos matemáticos' in content:
    print("✅ Linha 34 corrigida")
else:
    print("❌ Linha 34 ainda com problema")
EOF
