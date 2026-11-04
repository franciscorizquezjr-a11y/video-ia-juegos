import base64,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import numpy as np
from audio_quality import analyse_pcm,validate_sfx,AudioQualityError
from natural_narration import prepare_text,synthesize
class AudioQualityTests(unittest.TestCase):
 def voice(self,seconds=1):
  return .1*np.sin(np.arange(round(seconds*16000))*2*np.pi*227/16000)
 def test_short_breath_is_allowed_but_a_stall_is_blocked(self):
  self.assertTrue(analyse_pcm(np.concatenate([self.voice(),np.zeros(3200),self.voice()]),16000)['passed'])
  q=analyse_pcm(np.concatenate([self.voice(),np.zeros(12800),self.voice()]),16000)
  self.assertFalse(q['passed']);self.assertTrue(any('Internal silence' in e for e in q['errors']))
 def test_clipping_stretched_voice_and_missing_words_are_blocked(self):
  self.assertFalse(analyse_pcm(np.ones(16000),16000)['passed'])
  self.assertFalse(analyse_pcm(self.voice(),16000,playback_tempo=.8)['passed'])
  self.assertFalse(analyse_pcm(self.voice(),16000,[{'word':'go','start':0,'end':.5}],'Go to the depot')['passed'])
 def test_invalid_word_timing_and_trailing_silence_are_blocked(self):
  self.assertFalse(analyse_pcm(self.voice(),16000,[{'word':'go','start':.8,'end':1.6}])['passed'])
  self.assertFalse(analyse_pcm(np.concatenate([self.voice(),np.zeros(16000)]),16000)['passed'])
 def test_sound_needs_a_real_action_anchor_and_reason(self):
  self.assertTrue(validate_sfx([]))
  with self.assertRaises(AudioQualityError):validate_sfx([{'start':0,'duration':2.6}])
  self.assertTrue(validate_sfx([{'start':1,'anchor_time':1,'reason':'tool contacts the ground','duration':.12,'peak_limit':.025}]))
 def test_failed_candidate_does_not_replace_existing_voice(self):
  alignment={'characters':list('Hello world'),'character_start_times_seconds':[i*.04 for i in range(11)],'character_end_times_seconds':[(i+1)*.04 for i in range(11)]}
  response=Mock(status_code=200);response.json.return_value={'audio_base64':base64.b64encode(b'candidate').decode(),'normalized_alignment':alignment}
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'voice.mp3';p.write_bytes(b'old-voice')
   with patch('natural_narration.api_usage.usage_post',return_value=response) as call,patch('audio_quality.require_narration',side_effect=AudioQualityError('stalled')),patch('audio_quality.compact_file_pauses',side_effect=lambda p,words,**kw:(words,[])):
    with self.assertRaises(AudioQualityError):synthesize('Hello world',p,'fake')
   self.assertEqual(p.read_bytes(),b'old-voice');self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.kwargs['json']['voice_settings']['speed'],1.0)
   self.assertFalse(list(Path(d).glob('*.candidate_*')))
class PauseRepairTests(unittest.TestCase):
 def test_only_quiet_gap_is_removed_and_words_shift_without_stretching(self):
  from audio_quality import compact_aligned_pauses
  rate=16000;tone=.1*np.sin(np.arange(rate)*2*np.pi*227/rate)
  pcm=np.concatenate([tone,np.zeros(round(.8*rate)),tone])
  words=[{'word':'first','start':0,'end':1},{'word':'second','start':1.8,'end':2.8}]
  fixed,aligned,cuts=compact_aligned_pauses(pcm,rate,words)
  self.assertEqual(len(cuts),1);self.assertTrue(np.array_equal(fixed[:rate],tone));self.assertTrue(np.array_equal(fixed[-rate:],tone))
  self.assertAlmostEqual(aligned[1]['end']-aligned[1]['start'],1,places=4)
  self.assertTrue(analyse_pcm(fixed,rate,aligned)['passed'])
 def test_silence_overlapping_a_word_is_never_removed(self):
  from audio_quality import compact_aligned_pauses
  pcm=np.concatenate([np.ones(16000)*.1,np.zeros(12800),np.ones(16000)*.1])
  fixed,_,cuts=compact_aligned_pauses(pcm,16000,[{'word':'held','start':0,'end':2.8}])
  self.assertFalse(cuts);self.assertTrue(np.array_equal(fixed,pcm))
class NaturalNarrationTests(unittest.TestCase):
 def test_preserves_campaign_literal_and_punctuation(self):
  text='Wishlist now - Demo available. Wait, listen: now.'
  self.assertEqual(prepare_text(text),text)
 def test_avoids_forced_pauses_and_drawn_out_ellipsis(self):
  for text in ['Wait. <break time="0.25s" /> Now.','Wait... now.','[pause] Now.']:
   with self.assertRaises(ValueError):prepare_text(text)
if __name__=='__main__':unittest.main()
