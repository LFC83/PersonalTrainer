"""
API Module para integração com Telegram Bot
Este módulo expõe funções que podem ser chamadas pelo bot
"""
import os
import json
from datetime import date, timedelta
from historical_import import import_historical_data, get_client, fetch_hrv_data, fetch_sleep_data, fetch_daily_stats, save_data, update_consolidated_file

DATA_DIR = '/data'

def sync_historical(days: int = 7) -> dict:
    """
    Sincroniza dados históricos.
    
    Args:
        days: Número de dias para sincronizar
        
    Returns:
        dict: {'success': bool, 'message': str, 'days_imported': int}
    """
    try:
        result = import_historical_data(days)
        return {
            'success': True,
            'message': f'Dados históricos importados com sucesso',
            'days_requested': days
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao importar dados: {str(e)}',
            'days_requested': days
        }

def get_latest_data() -> dict:
    """
    Retorna os dados mais recentes disponíveis.
    
    Returns:
        dict: Dados mais recentes ou None se não existir
    """
    try:
        consolidated_path = os.path.join(DATA_DIR, "garmin_data_consolidated.json")
        
        if not os.path.exists(consolidated_path):
            return {'success': False, 'message': 'Nenhum dado disponível'}
        
        with open(consolidated_path, 'r') as f:
            all_data = json.load(f)
        
        if not all_data or len(all_data) == 0:
            return {'success': False, 'message': 'Nenhum dado disponível'}
        
        latest = all_data[0]  # já está ordenado por data (mais recente primeiro)
        
        return {
            'success': True,
            'data': latest,
            'date': latest.get('date')
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao ler dados: {str(e)}'
        }

def get_data_by_date(target_date: str) -> dict:
    """
    Retorna dados de uma data específica.
    
    Args:
        target_date: Data no formato YYYY-MM-DD
        
    Returns:
        dict: Dados da data solicitada
    """
    try:
        filename = os.path.join(DATA_DIR, f"garmin_data_{target_date}.json")
        
        if not os.path.exists(filename):
            return {
                'success': False,
                'message': f'Dados não encontrados para {target_date}'
            }
        
        with open(filename, 'r') as f:
            data = json.load(f)
        
        return {
            'success': True,
            'data': data,
            'date': target_date
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao ler dados: {str(e)}'
        }

def get_data_range(start_date: str, end_date: str) -> dict:
    """
    Retorna dados de um intervalo de datas.
    
    Args:
        start_date: Data inicial (YYYY-MM-DD)
        end_date: Data final (YYYY-MM-DD)
        
    Returns:
        dict: Lista de dados do período
    """
    try:
        consolidated_path = os.path.join(DATA_DIR, "garmin_data_consolidated.json")
        
        if not os.path.exists(consolidated_path):
            return {'success': False, 'message': 'Nenhum dado disponível'}
        
        with open(consolidated_path, 'r') as f:
            all_data = json.load(f)
        
        # Filtrar por intervalo de datas
        filtered_data = [
            d for d in all_data 
            if start_date <= d.get('date', '') <= end_date
        ]
        
        return {
            'success': True,
            'data': filtered_data,
            'count': len(filtered_data),
            'start_date': start_date,
            'end_date': end_date
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao ler dados: {str(e)}'
        }

def get_hrv_summary(days: int = 7) -> dict:
    """
    Retorna resumo de HRV dos últimos N dias.
    
    Args:
        days: Número de dias para análise
        
    Returns:
        dict: Estatísticas de HRV
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        range_data = get_data_range(
            start_date.isoformat(),
            end_date.isoformat()
        )
        
        if not range_data['success']:
            return range_data
        
        hrv_values = []
        for day_data in range_data['data']:
            if day_data.get('hrv') and isinstance(day_data['hrv'], dict):
                # Extrair valor de HRV (pode variar dependendo da estrutura retornada)
                hrv = day_data['hrv']
                if 'lastNightAvg' in hrv:
                    hrv_values.append(hrv['lastNightAvg'])
        
        if not hrv_values:
            return {
                'success': False,
                'message': 'Nenhum dado de HRV disponível no período'
            }
        
        return {
            'success': True,
            'days': days,
            'avg_hrv': sum(hrv_values) / len(hrv_values),
            'min_hrv': min(hrv_values),
            'max_hrv': max(hrv_values),
            'count': len(hrv_values),
            'values': hrv_values
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao calcular resumo de HRV: {str(e)}'
        }

def force_sync_today() -> dict:
    """
    Força sincronização dos dados de hoje.
    
    Returns:
        dict: Resultado da sincronização
    """
    try:
        client = get_client()
        today = date.today()
        
        hrv_data = fetch_hrv_data(client, today)
        sleep_data = fetch_sleep_data(client, today)
        stats_data = fetch_daily_stats(client, today)
        
        save_data(today, hrv_data, sleep_data, stats_data)
        update_consolidated_file()
        
        return {
            'success': True,
            'message': 'Dados de hoje sincronizados com sucesso',
            'date': today.isoformat()
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro ao sincronizar: {str(e)}'
        }
