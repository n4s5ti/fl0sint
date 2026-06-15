"""
Social Confidence Aggregator Enricher
=====================================
Aggregates SocialAccount outputs from multiple email→social enrichers.
Deduplicates by platform+profile_url and computes confidence scores
using the E2→E3 evidence ladder from pivot?.md.
"""
from typing import List, Dict, Any
from flowsint_core.core.enricher_base import Enricher
from flowsint_enrichers import flowsint_enricher
from flowsint_types.social_account import SocialAccount

@flowsint_enricher
class SocialConfidenceAggregator(Enricher):
    """Aggregates social discovery results from multiple enrichers,
    deduplicating by platform+profile_url and computing confidence
    via multi-tool cross-validation (E2→E3 evidence ladder)."""
    
    InputType = SocialAccount  # Takes list of SocialAccount (collected from all tools)
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "social-confidence-aggregator"

    @classmethod
    def category(cls) -> str:
        return "Email"

    @classmethod
    def key(cls) -> str:
        return "social-confidence-aggregator"

    async def scan(self, values: List[SocialAccount]) -> List[Dict[str, Any]]:
        """Aggregate and score social accounts."""
        # Group by (platform, profile_url) for dedup
        groups: Dict[str, Dict[str, Any]] = {}
        
        for account in values:
            key = f"{account.platform}|{account.profile_url}"
            if key not in groups:
                groups[key] = {
                    "platform": account.platform,
                    "profile_url": account.profile_url,
                    "username": account.username,
                    "bio": account.bio,
                    "followers_count": account.followers_count,
                    "following_count": account.following_count,
                    "verified": account.verified,
                    "location": getattr(account, 'location', None),
                    "hit_count": 1,
                    "source_tools": [getattr(account, 'enricher_name', 'unknown')],
                    "confidence_score": 0.65,  # base E2 confidence
                    "evidence_level": "E2",
                }
            else:
                existing = groups[key]
                existing["hit_count"] += 1
                tool_name = getattr(account, 'enricher_name', 'unknown')
                if tool_name not in existing["source_tools"]:
                    existing["source_tools"].append(tool_name)
                
                # Confidence boost per additional independent tool
                # Base 0.65 + 0.15 per extra tool, capped at 0.95
                existing["confidence_score"] = min(0.95, 0.65 + 0.15 * (existing["hit_count"] - 1))
                
                # Evidence level: 2+ tools = E3 cross-verified
                if existing["hit_count"] >= 2:
                    existing["evidence_level"] = "E3"
        
        results = []
        for key, data in groups.items():
            results.append({
                "platform": data["platform"],
                "profile_url": data["profile_url"],
                "username": data["username"],
                "bio": data["bio"],
                "followers_count": data["followers_count"],
                "following_count": data["following_count"],
                "verified": data["verified"],
                "location": data["location"],
                "hit_count": data["hit_count"],
                "source_tools": data["source_tools"],
                "confidence_score": data["confidence_score"],
                "evidence_level": data["evidence_level"],
            })
        
        # Create nodes for results with E2+ confidence
        for r in results:
            if r["confidence_score"] >= 0.65:
                social = SocialAccount(
                    platform=r["platform"],
                    profile_url=r["profile_url"],
                    username=r.get("username", ""),
                    bio=r.get("bio"),
                    followers_count=r.get("followers_count"),
                    following_count=r.get("following_count"),
                    verified=r.get("verified", False),
                    location=r.get("location"),
                    nodeLabel=f"{r['platform']}: {r.get('username', r['profile_url'])} [{r['evidence_level']}]",
                    confidence_score=r["confidence_score"],
                    hit_count=r["hit_count"],
                    source_tools=r["source_tools"],
                    evidence_level=r["evidence_level"],
                )
                self.create_node(social)
        
        return results
