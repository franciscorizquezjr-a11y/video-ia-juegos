import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from youtube_metadata import build_metadata,rank_titles,choose_hashtags,generation_prompt
SOURCE='Moon Sweepers is a co-op cleaning simulation for up to sixteen players on PC and Steam. Collect lunar dust and upgrade tools.'
class MetadataTests(unittest.TestCase):
 def test_relevant_tags_are_unique_and_no_viral_default(self):
  tags,warnings=choose_hashtags('Moon Sweepers',SOURCE,['#ViralShorts','#FYP','#CoopGames','#CoopGames'])
  self.assertEqual(len(tags),len(set(t.lower() for t in tags)));self.assertLessEqual(len(tags),5)
  self.assertIn('#Shorts',tags);self.assertIn('#MoonSweepers',tags);self.assertIn('#CoopGames',tags)
  self.assertNotIn('#ViralShorts',tags);self.assertTrue(warnings)
 def test_supported_number_words_and_misleading_claims(self):
  good,bad=rank_titles('Moon Sweepers',SOURCE,['Sixteen Cleaners | Moon Sweepers','64 Players. Free Demo. | Moon Sweepers','Moon Sweepers #Shorts'])
  self.assertEqual(len(good),1);self.assertEqual(len(bad),2)
 def test_short_specific_title_beats_generic_all_caps(self):
  ranked,_=rank_titles('Moon Sweepers',SOURCE,['CHECK OUT THIS AMAZING NEW GAME RIGHT NOW MOON SWEEPERS','Moon Cleanup | Moon Sweepers'])
  self.assertEqual(ranked[0]['title'],'Moon Cleanup | Moon Sweepers')
 def test_campaign_keeps_cta_disclosure_and_mandatory_tags(self):
  m=build_metadata('Miner Shop Simulator','Collect ore. Sell it in your shop.',briefing='PC Steam simulation.',sponsored=True,required_cta='Wishlist now - Demo available',required_hashtags=['#MinerCampaign'])
  self.assertIn('Wishlist now - Demo available',m['description']);self.assertIn('#ad',m['description']);self.assertIn('#MinerCampaign',m['hashtags'])
 def test_organic_fallback_does_not_invent_demo_or_ad(self):
  m=build_metadata('Moon Sweepers','Collect lunar dust. Upgrade tools.',candidate_titles=['Moon Cleanup | Moon Sweepers'])
  self.assertEqual(m['title'],'Moon Cleanup | Moon Sweepers')
  self.assertNotIn('#ad',m['description']);self.assertNotIn('demo',m['description'].lower());self.assertTrue(m['review_required'])
  self.assertFalse(m['publishing']['unlisted_required_for_algorithm']);self.assertFalse(m['thumbnail']['insert_still_frame_default'])
 def test_limits_and_missing_context_fail_safely(self):
  ranked,bad=rank_titles('Moon Sweepers',SOURCE,['x'*101,'Moon Cleanup 🪥 🚀 | Moon Sweepers'])
  self.assertFalse(ranked);self.assertEqual(len(bad),2)
  with self.assertRaises(ValueError):build_metadata('Moon Sweepers')
 def test_competitive_game_is_not_tagged_as_coop(self):
  tags,_=choose_hashtags('Golf With Your Friends 2','Competitive multiplayer, not cooperative. PC Steam mini golf.')
  self.assertNotIn('#CoopGames',tags)
class MetadataApiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from fastapi.testclient import TestClient
  import web_server
  cls.server=web_server;cls.client=TestClient(web_server.app)
 def test_api_is_local_by_default_and_does_not_save_or_publish(self):
  with patch('web_server._api_usage.usage_post') as call:
   result=self.client.post('/api/youtube-shorts/metadata',json={'game_name':'Moon Sweepers','script_text':SOURCE,'candidate_titles':['Moon Cleanup | Moon Sweepers']})
  self.assertEqual(result.status_code,200);call.assert_not_called();self.assertIn('title_candidates',result.json())
 def test_save_campaign_requires_disclosure_and_literal_cta(self):
  response=self.client.post('/api/youtube-shorts/save-metadata',json={'game_name':'Miner','title':'Miner shop','description':'A shop.','sponsored':True,'required_cta':'Wishlist now - Demo available'})
  self.assertEqual(response.status_code,400)
 def test_saving_texts_keeps_the_selected_edit(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch.object(self.server,'APP_OUTPUT_DIR',Path(d)):
   response=self.client.post('/api/youtube-shorts/save-metadata',json={'game_name':'Moon Sweepers','title':'My selected title','description':'My reviewed description. #Shorts #MoonSweepers'})
   self.assertEqual(response.status_code,200);self.assertFalse(response.json()['published'])
   self.assertEqual((Path(d)/'Promo_Moon Sweepers_latest/YouTube_titulo.txt').read_text().strip(),'My selected title')
if __name__=='__main__':unittest.main()
