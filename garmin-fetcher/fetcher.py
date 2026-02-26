import os
import json
import time
from datetime import date, timedelta
from garminconnect import Garmin, GarminConnectAuthenticationError

# Credenciais via variáveis de ambiente
GARMIN_EMAIL = os.getenv('GARMIN_EMAIL')
GARMIN_PASSWORD = os.getenv('GARMIN_PASSWORD')
DATA_DIR = '/data'

def get_client() -> Garmin:
    """Autentica no Garmin Connect"""
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        raise ValueError("❌ GARMIN_EMAIL e GARMIN_PASSWORD devem estar definidos")
    
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    print("✅ Autenticado no Garmin Connect")
    return client

def fetch_hrv_data(client: Garmin, target_date: date) -> dict:
    """Busca dados de HRV para uma data específica"""
    date_str = target_date.isoformat()
    try:
        hrv_data = client.get_hrv_data(date_str)
        print(f"✅ HRV data obtido para {date_str}")
        return hrv_data
    except Exception as e:
        print(f"⚠️  Erro ao obter HRV para {date_str}: {e}")
        return None

def fetch_sleep_data(client: Garmin, target_date: date) -> dict:
    """Busca dados de sono para uma data específica"""
    date_str = target_date.isoformat()
    try:
        sleep_data = client.get_sleep_data(date_str)
        print(f"✅ Sleep data obtido para {date_str}")
        return sleep_data
    except Exception as e:
        print(f"⚠️  Erro ao obter Sleep para {date_str}: {e}")
        return None

def fetch_daily_stats(client: Garmin, target_date: date) -> dict:
    """Busca estatísticas diárias"""
    date_str = target_date.isoformat()
    try:
        stats = client.get_stats(date_str)
        print(f"✅ Stats obtidos para {date_str}")
        return stats
    except Exception as e:
        print(f"⚠️  Erro ao obter Stats para {date_str}: {e}")
        return None

def fetch_activities(client: Garmin, days: int = 7) -> list:
    """
    Busca atividades dos últimos N dias.
    Retorna lista de atividades formatadas.
    """
    try:
        # Garmin API retorna atividades por limite (não por data)
        # Buscar mais atividades para garantir cobertura dos últimos N dias
        limit = days * 3  # Assumir ~3 atividades por dia no máximo
        
        print(f"🏃 A buscar atividades (limit={limit})...")
        activities = client.get_activities(0, limit)
        
        if not activities:
            print("⚠️  Nenhuma atividade encontrada")
            return []
        
        # Filtrar apenas atividades dos últimos N dias
        cutoff_date = date.today() - timedelta(days=days)
        filtered = []
        
        for act in activities:
            # Extrair data da atividade
            start_time = act.get('startTimeLocal') or act.get('startTimeGMT') or act.get('beginTimestamp')
            if not start_time:
                continue
            
            # Converter para date
            if 'T' in start_time:
                act_date_str = start_time.split('T')[0]
            else:
                act_date_str = start_time[:10]
            
            try:
                act_date = date.fromisoformat(act_date_str)
            except:
                continue
            
            # Filtrar por data
            if act_date >= cutoff_date:
                filtered.append(act)
        
        print(f"✅ {len(filtered)} atividades encontradas nos últimos {days} dias")
        return filtered
        
    except Exception as e:
        print(f"❌ Erro ao buscar atividades: {e}")
        import traceback
        traceback.print_exc()
        return []

def save_activities(activities: list):
    """
    Salva atividades em activities.json.
    Faz merge com atividades existentes para evitar duplicados.
    """
    try:
        activities_path = os.path.join(DATA_DIR, 'activities.json')
        
        # Carregar atividades existentes
        existing = []
        if os.path.exists(activities_path):
            try:
                with open(activities_path, 'r') as f:
                    existing = json.load(f)
            except Exception as e:
                print(f"⚠️  Erro ao carregar activities.json existente: {e}")
                existing = []
        
        # Criar índice de IDs existentes para evitar duplicados
        existing_ids = set()
        for act in existing:
            act_id = act.get('activityId') or act.get('id')
            if act_id:
                existing_ids.add(act_id)
        
        # Adicionar novas atividades (evitar duplicados)
        added_count = 0
        for act in activities:
            act_id = act.get('activityId') or act.get('id')
            if act_id and act_id not in existing_ids:
                existing.append(act)
                existing_ids.add(act_id)
                added_count += 1
        
        # Ordenar por data (mais recente primeiro)
        def get_activity_date(act):
            start_time = act.get('startTimeLocal') or act.get('startTimeGMT') or act.get('beginTimestamp') or ''
            if 'T' in start_time:
                return start_time.split('T')[0]
            return start_time[:10] if len(start_time) >= 10 else '0000-00-00'
        
        existing.sort(key=get_activity_date, reverse=True)
        
        # Limitar a 100 atividades mais recentes
        existing = existing[:100]
        
        # Salvar
        with open(activities_path, 'w') as f:
            json.dump(existing, f, indent=2)
        
        print(f"💾 Activities.json atualizado: {added_count} novas atividades ({len(existing)} total)")
        
    except Exception as e:
        print(f"❌ Erro ao salvar atividades: {e}")
        import traceback
        traceback.print_exc()

def save_data(target_date: date, hrv_data: dict, sleep_data: dict, stats_data: dict):
    """Guarda os dados em arquivos JSON organizados por data"""
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str = target_date.isoformat()
    
    consolidated = {
        "date": date_str,
        "hrv": hrv_data,
        "sleep": sleep_data,
        "stats": stats_data,
        "fetched_at": time.time()
    }
    
    filename = os.path.join(DATA_DIR, f"garmin_data_{date_str}.json")
    with open(filename, 'w') as f:
        json.dump(consolidated, f, indent=2)
    
    print(f"💾 Dados guardados em {filename}")
    
    update_consolidated_file()

def update_consolidated_file():
    """Cria/atualiza arquivo consolidado com dados dos últimos 30 dias"""
    try:
        consolidated_path = os.path.join(DATA_DIR, "garmin_data_consolidated.json")
        all_data = []
        
        for filename in sorted(os.listdir(DATA_DIR)):
            if filename.startswith("garmin_data_") and filename.endswith(".json") and filename != "garmin_data_consolidated.json":
                filepath = os.path.join(DATA_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            all_data.append(data)
                        elif isinstance(data, list) and len(data) > 0:
                            if isinstance(data[0], dict):
                                all_data.append(data[0])
                except Exception as e:
                    print(f"⚠️  Erro ao ler {filename}: {e}")
        
        all_data.sort(key=lambda x: x.get('date', ''), reverse=True)
        all_data = all_data[:30]
        
        with open(consolidated_path, 'w') as f:
            json.dump(all_data, f, indent=2)
        
        print(f"💾 Arquivo consolidado atualizado com {len(all_data)} dias")
    except Exception as e:
        print(f"⚠️  Erro ao atualizar arquivo consolidado: {e}")

def fetch_and_save_today():
    """Busca e guarda dados de hoje"""
    try:
        client = get_client()
        today = date.today()
        
        print(f"\n📊 A buscar dados para {today.isoformat()}...")
        
        hrv_data = fetch_hrv_data(client, today)
        sleep_data = fetch_sleep_data(client, today)
        stats_data = fetch_daily_stats(client, today)
        
        save_data(today, hrv_data, sleep_data, stats_data)
        
        # NOVO: Buscar atividades dos últimos 7 dias
        print(f"\n🏃 A buscar atividades recentes...")
        activities = fetch_activities(client, days=7)
        if activities:
            save_activities(activities)
        
        print("✅ Sincronização completa!")
        return True
        
    except GarminConnectAuthenticationError as e:
        print(f"❌ Erro de autenticação: {e}")
        print("   Verifique GARMIN_EMAIL e GARMIN_PASSWORD")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_and_process_flags():
    """
    Verifica se existem pedidos de sincronização ou importação via flags
    """
    # Flag de sincronização
    sync_flag = os.path.join(DATA_DIR, 'sync_request.json')
    if os.path.exists(sync_flag):
        try:
            print("🚩 Flag de sincronização detectada")
            with open(sync_flag, 'r') as f:
                request = json.load(f)
            
            if request.get('status') == 'pending':
                print("▶️  A processar pedido de sincronização...")
                success = fetch_and_save_today()
                
                request['status'] = 'completed' if success else 'failed'
                request['processed_at'] = time.time()
                
                with open(sync_flag, 'w') as f:
                    json.dump(request, f, indent=2)
                
                print("✅ Flag de sincronização processada")
        except Exception as e:
            print(f"⚠️  Erro ao processar flag de sincronização: {e}")
    
    # Flag de importação
    import_flag = os.path.join(DATA_DIR, 'import_request.json')
    if os.path.exists(import_flag):
        try:
            print("🚩 Flag de importação detectada")
            with open(import_flag, 'r') as f:
                request = json.load(f)
            
            if request.get('status') == 'pending':
                days = request.get('days', 7)
                print(f"▶️  A processar pedido de importação ({days} dias)...")
                
                # Importar histórico
                from historical_import import import_historical_data
                success = import_historical_data(days)
                
                request['status'] = 'completed' if success else 'failed'
                request['processed_at'] = time.time()
                
                with open(import_flag, 'w') as f:
                    json.dump(request, f, indent=2)
                
                print("✅ Flag de importação processada")
        except Exception as e:
            print(f"⚠️  Erro ao processar flag de importação: {e}")

def main():
    """Loop principal - executa a cada hora"""
    print("🚀 Garmin Fetcher v2.1 iniciado")
    print(f"   Email: {GARMIN_EMAIL}")
    print(f"   Data dir: {DATA_DIR}")
    print(f"   Modo: Loop com verificação de flags + atividades")
    
    while True:
        try:
            # Verificar e processar flags primeiro
            check_and_process_flags()
            
            # Depois fazer sync normal
            fetch_and_save_today()
            
            print("⏳ Próxima atualização em 1h...")
            time.sleep(3600)  # 1 hora
            
        except KeyboardInterrupt:
            print("\n🛑 Fetcher interrompido pelo utilizador")
            break
        except Exception as e:
            print(f"❌ Erro no loop principal: {e}")
            import traceback
            traceback.print_exc()
            print("⏳ A tentar novamente em 5 minutos...")
            time.sleep(300)  # 5 minutos em caso de erro

if __name__ == "__main__":
    main()
