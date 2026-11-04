from pathlib import Path
import requests,json,concurrent.futures,datetime
B=Path(r'E:\proyecto_videos\ia_video\docs\investigacion_crecimiento_2026-10-31\snapshot_reproducible');B.mkdir(exist_ok=True)
apps={'probably_stolen':4348910,'moon_sweepers':5335510,'repo':3241660,'peak':3527290,'rv_there_yet':3949040,'scam_with_friends':4954910,'nomad_drive':2223980,'atmosfar':1798230,'slay_spire2':2868840,'factorio':427520,'project_zomboid':108600,'no_mans_sky':275850,'cairn':1588550,'outlast_trials':1304930,'escape_backrooms':1943950,'helldivers2':553850,'sons_forest':1326470,'light_no_fire':2719590,'content_warning':2881650,'lethal_company':1966720,'timberborn':1062090,'valheim':892970,'enshrouded':1203620}
def get(url,params):
 r=requests.get(url,params=params,timeout=25);r.raise_for_status();return r.json()
def collect(pair):
 slug,appid=pair;f=B/(slug+'.json')
 if f.exists():return json.loads(f.read_text(encoding='utf-8'))
 row={'slug':slug,'appid':appid,'store_url':f'https://store.steampowered.com/app/{appid}/','research_date_madrid':'2026-10-31','retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'errors':[]}
 try:
  blob=get('https://store.steampowered.com/api/appdetails',{'appids':appid,'l':'english','cc':'US'});detail=blob.get(str(appid),{})
  if detail.get('success'):
   row['store_data']=detail['data'];row['name']=detail['data']['name']
  else:row['errors'].append('store_data_unavailable')
 except Exception as e:row['errors'].append('store:'+type(e).__name__)
 try:
  data=get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/',{'appid':appid});row['players_raw']=data
  response=data.get('response',{});row['current_players']=response.get('player_count') if response.get('result')==1 else None
 except Exception as e:row['errors'].append('players:'+type(e).__name__)
 try:
  data=get(f'https://store.steampowered.com/appreviews/{appid}',{'json':1,'filter':'recent','language':'all','purchase_type':'all','num_per_page':1});row['review_summary']=data.get('query_summary');row['review_summary_interpretation']='Returned Steam summary for this query, not a measured 30-day growth rate. No review text stored.'
 except Exception as e:row['errors'].append('reviews:'+type(e).__name__)
 try:
  data=get('https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/',{'appid':appid,'count':3,'maxlength':280,'feeds':'steam_community_announcements'});row['official_news']=data.get('appnews',{}).get('newsitems',[])
 except Exception as e:row['errors'].append('news:'+type(e).__name__)
 f.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return row
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 for f in concurrent.futures.as_completed([ex.submit(collect,p) for p in apps.items()]):
  row=f.result();rows.append(row);print(json.dumps({'name':row.get('name',row['slug']),'players':row.get('current_players'),'errors':row['errors']},ensure_ascii=True),flush=True)
(B/'snapshot.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('RESEARCH_SNAPSHOT_READY',len(rows),flush=True)
