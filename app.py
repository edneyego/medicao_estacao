from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from bs4 import BeautifulSoup
import threading
import requests
import time
import os
import logging

load_dotenv()

# Configuração de log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB Configuration
client = MongoClient(os.getenv("MONGO_URI"))
db = client.station_data
FTP_HOST = os.getenv("FTP_HOST")

# Processamento HTTP (uma execução)
def process_http():
    try:
        response = requests.get(FTP_HOST)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all('a')

        txt_files = [
            link.get('href') for link in links
            if link.get('href') and link.get('href').endswith('.txt')
        ]

        for filename in txt_files:
            file_url = f"{FTP_HOST}/{filename}"
            try:
                file_resp = requests.get(file_url)
                file_resp.raise_for_status()
                lines = file_resp.text.strip().split('\n')
                records = []
                for line in lines:
                    parts = line.strip().split(',')
                    if len(parts) >= 3:
                        code, timestamp_str, measurement = parts[:3]
                        try:
                            timestamp = datetime.strptime(timestamp_str, '%d/%m/%Y %H:%M')
                            measurement_value = float(measurement)
                            records.append({
                                'station_code': code,
                                'timestamp': timestamp,
                                'measurement': measurement_value,
                                'source': 'http',
                                'filename': filename
                            })
                        except ValueError:
                            continue
                inserted_count = 0
                for record in records:
                    exists = db.measurements.find_one({
                        'station_code': record['station_code'],
                        'timestamp': record['timestamp'],
                        'measurement': record['measurement'],
                        'filename': record['filename']
                    })
                    if not exists:
                        db.measurements.insert_one(record)
                        inserted_count += 1
                logger.info(f"{inserted_count} novos registros inseridos via HTTP de {filename}")
            except Exception as e:
                logger.warning(f"Erro ao processar {filename}: {e}")

    except requests.RequestException as e:
        logger.error(f"Erro HTTP ao acessar diretório: {e}")
    except Exception as e:
        logger.error(f"Erro inesperado no processamento HTTP: {e}")

# Loop infinito de coleta HTTP
def fetch_http_data():
    while True:
        process_http()
        time.sleep(43200)

# Lifespan para inicialização das threads
@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=fetch_http_data, daemon=True).start()
    logger.info("Thread de coleta HTTP iniciada.")
    yield
    logger.info("Aplicacão encerrando.")

app = FastAPI(lifespan=lifespan)

# Rotas FastAPI
@app.get("/estacao/media/{mes}/{ano}/{codigo}")
def get_monthly_avg(mes:str, ano: str, codigo: str):
    try:
        mes, ano = int(mes), int(ano)
        start_date = datetime(ano, mes, 1)
        end_date = (start_date + timedelta(days=32)).replace(day=1)

        pipeline = [
            {'$match': {'station_code': codigo, 'timestamp': {'$gte': start_date, '$lt': end_date}}},
            {'$group': {'_id': None, 'average_measurement': {'$avg': '$measurement'}}}
        ]

        result = list(db.measurements.aggregate(pipeline))
        avg = result[0]['average_measurement'] if result else None

        return JSONResponse(content={'station_code': codigo, 'monthly_avg': avg})
    except Exception as e:
        logger.error(f"Erro ao buscar média mensal: {e}")
        return JSONResponse(status_code=500, content={"error": "Erro interno ao processar a requisição."})

@app.get("/estacao/media/{ano}/{codigo}")
def get_annual_avg(ano: int, codigo: str):
    try:
        start_date = datetime(ano, 1, 1)
        end_date = datetime(ano + 1, 1, 1)

        pipeline = [
            {'$match': {'station_code': codigo, 'timestamp': {'$gte': start_date, '$lt': end_date}}},
            {'$group': {'_id': None, 'average_measurement': {'$avg': '$measurement'}}}
        ]

        result = list(db.measurements.aggregate(pipeline))
        avg = result[0]['average_measurement'] if result else None

        return JSONResponse(content={'station_code': codigo, 'annual_avg': avg})
    except Exception as e:
        logger.error(f"Erro ao buscar média anual: {e}")
        return JSONResponse(status_code=500, content={"error": "Erro interno ao processar a requisição."})

@app.get("/estacao/{mes}/{ano}/{codigo}")
def get_all_measurements(mes:str ,ano: str, codigo: str):
    try:
        mes, ano = int(mes), int(ano)
        start_date = datetime(ano, mes, 1)
        end_date = (start_date + timedelta(days=32)).replace(day=1)

        cursor = db.measurements.find({
            'station_code': codigo,
            'timestamp': {'$gte': start_date, '$lt': end_date}
        })

        result = []
        for doc in cursor:
            result.append({
                'station_code': doc['station_code'],
                'timestamp': doc['timestamp'].isoformat(),
                'measurement': doc['measurement'],
                'filename': doc.get('filename'),
                'source': doc.get('source')
            })

        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Erro ao buscar medições do mês: {e}")
        return JSONResponse(status_code=500, content={"error": "Erro interno ao processar a requisição."})


@app.post("/estacao/start")
def manual_trigger():
    threading.Thread(target=process_http).start()
    return {"message": "Coleta manual iniciada em background"}

