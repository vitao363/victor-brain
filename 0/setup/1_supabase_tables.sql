-- ============================================================
-- VICTOR BRAIN - SUPABASE SETUP
-- Execute este script no SQL Editor do Supabase
-- ============================================================

-- 1. TABELA PRINCIPAL DE ITENS
CREATE TABLE IF NOT EXISTS items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tipo TEXT NOT NULL,
    texto TEXT NOT NULL,
    categoria TEXT,
    empresa TEXT DEFAULT 'pessoal',
    prioridade INTEGER DEFAULT 3 CHECK (prioridade BETWEEN 1 AND 5),
    status TEXT DEFAULT 'pendente',
    tags TEXT[] DEFAULT '{}',
    pessoas TEXT[] DEFAULT '{}',
    prazo DATE,
    criado_em TIMESTAMPTZ DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ DEFAULT NOW(),
    concluido_em TIMESTAMPTZ,
    fonte TEXT DEFAULT 'telegram',
    msg_original TEXT
);

-- 2. RAIO-X (contexto dinâmico do Victor)
CREATE TABLE IF NOT EXISTS raio_x (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    chave TEXT UNIQUE NOT NULL,
    valor TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ DEFAULT NOW()
);

-- 3. LOG DE MENSAGENS
CREATE TABLE IF NOT EXISTS log_mensagens (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_msg_id BIGINT,
    conteudo_raw TEXT,
    conteudo_processado TEXT,
    items_gerados INTEGER DEFAULT 0,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

-- 4. CATEGORIAS
CREATE TABLE IF NOT EXISTS categorias (
    slug TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    cor TEXT,
    icone TEXT,
    obsidian_folder TEXT
);

-- INDEXES para performance
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_empresa ON items(empresa);
CREATE INDEX IF NOT EXISTS idx_items_prioridade ON items(prioridade);
CREATE INDEX IF NOT EXISTS idx_items_criado_em ON items(criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_items_tipo ON items(tipo);

-- ROW LEVEL SECURITY (habilitar)
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE raio_x ENABLE ROW LEVEL SECURITY;
ALTER TABLE log_mensagens ENABLE ROW LEVEL SECURITY;
ALTER TABLE categorias ENABLE ROW LEVEL SECURITY;

-- Policies: permite tudo com a chave de serviço (anon key do bot)
CREATE POLICY "Allow all for anon" ON items FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON raio_x FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON log_mensagens FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON categorias FOR ALL TO anon USING (true) WITH CHECK (true);

-- TRIGGER para atualizar atualizado_em automaticamente
CREATE OR REPLACE FUNCTION update_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_items_atualizado_em
    BEFORE UPDATE ON items
    FOR EACH ROW
    EXECUTE FUNCTION update_atualizado_em();

-- CATEGORIAS iniciais
INSERT INTO categorias (slug, nome, cor, icone, obsidian_folder) VALUES
('crm', 'CRM', '#4CAF50', '📊', 'CRM'),
('growth', 'Growth', '#2196F3', '📈', 'Growth'),
('produto', 'Produto', '#9C27B0', '🛠️', 'Produto'),
('lideranca', 'Liderança', '#FF9800', '👥', 'Lideranca'),
('saude', 'Saúde', '#F44336', '💪', 'Saude'),
('financeiro', 'Financeiro', '#607D8B', '💰', 'Financeiro'),
('pessoal', 'Pessoal', '#795548', '👤', 'Pessoal'),
('projeto', 'Projeto IA', '#00BCD4', '🚀', 'Projetos')
ON CONFLICT (slug) DO NOTHING;
