"""Voice management for TTS"""

from typing import Dict, Optional, List
from anees.models import VoiceInfo


class VoiceManager:
    def __init__(self):
        self.catalog: Dict[str, Dict[str, Dict]] = {
            "arabic": {
                "af_msa": {
                    "name": "Modern Standard Arabic (Female)",
                    "gender": "female",
                    "style": "academic",
                    "recommended": True
                },
                "am_msa": {
                    "name": "Modern Standard Arabic (Male)",
                    "gender": "male",
                    "style": "academic",
                    "recommended": False
                }
            },
            "english": {
                "af_bella": {
                    "name": "US English (Female)",
                    "gender": "female",
                    "style": "academic",
                    "recommended": True
                },
                "am_michael": {
                    "name": "US English (Male)",
                    "gender": "male",
                    "style": "academic",
                    "recommended": False
                },
                "bf_emma": {
                    "name": "UK English (Female)",
                    "gender": "female",
                    "style": "narrative",
                    "recommended": False
                }
            },
            "saudi": {
                "af_saudi": {
                    "name": "Saudi Arabic (Female)",
                    "gender": "female",
                    "style": "narrative",
                    "recommended": False
                }
            }
        }

    def get_catalog(self) -> Dict:
        """Return full voice catalog"""
        result = {}
        for lang, voices in self.catalog.items():
            result[lang] = {}
            for vid, info in voices.items():
                result[lang][vid] = {
                    "id": vid,
                    "name": info["name"],
                    "language": lang,
                    "gender": info["gender"],
                    "style": info["style"],
                    "recommended": info["recommended"]
                }
        return result

    def get_voice_info(self, voice_id: str) -> Optional[Dict]:
        for lang_voices in self.catalog.values():
            if voice_id in lang_voices:
                return {"id": voice_id, **lang_voices[voice_id]}
        return None

    def get_voices_by_language(self, lang: str) -> List[VoiceInfo]:
        voices = self.catalog.get(lang, {})
        return [
            VoiceInfo(id=vid, **info)
            for vid, info in voices.items()
        ]

    def get_recommended_voice(self, lang: str) -> Optional[str]:
        voices = self.catalog.get(lang, {})
        for vid, info in voices.items():
            if info.get("recommended"):
                return vid
        return next(iter(voices.keys())) if voices else None