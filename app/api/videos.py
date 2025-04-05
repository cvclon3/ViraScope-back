# app/api/videos.py

from fastapi import APIRouter, Depends, HTTPException, status, Body, Query # Import Query
from typing import List, Dict, Optional
from googleapiclient.discovery import build
import logging
import traceback

from app.api.auth import get_user_youtube_client_via_cookie
from app.models.search_models import Item, SearchResponse
from app.core.youtube import get_channel_info, parse_duration, get_total_videos_on_channel

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# --- Helper Function (build_item_from_video_details - remains the same) ---
async def build_item_from_video_details(
    youtube: build,
    video_detail: Dict,
    channel_cache: Dict[str, Optional[Dict]] # Cache for channel info within the request
) -> Optional[Item]:
    """
    Builds an Item object from YouTube video details and cached channel info.
    Fetches channel info if not already cached.
    """
    video_id = video_detail.get('id')
    snippet = video_detail.get('snippet', {})
    statistics = video_detail.get('statistics', {})
    content_details = video_detail.get('contentDetails', {})
    channel_id = snippet.get('channelId')

    if not video_id or not channel_id:
        logger.warning(f"Skipping video due to missing video_id or channel_id. Video data: {video_detail}")
        return None

    try:
        # --- Channel Info Handling ---
        if channel_id not in channel_cache:
            logger.info(f"Fetching channel info for {channel_id} (not in cache)...")
            channel_info_dict = await get_channel_info(youtube, channel_id)
            channel_cache[channel_id] = channel_info_dict # Cache result (even if None)
        else:
            logger.debug(f"Using cached channel info for {channel_id}.")
            channel_info_dict = channel_cache[channel_id]

        if not channel_info_dict:
            logger.warning(f"Could not get channel info for {channel_id} (video_id: {video_id}). Skipping item.")
            return None

        # --- Video Stats ---
        likes = int(statistics['likeCount']) if 'likeCount' in statistics else 0
        likes_hidden = 'likeCount' not in statistics
        views = int(statistics.get('viewCount', 0))
        comments = int(statistics['commentCount']) if 'commentCount' in statistics else 0
        comments_hidden = 'commentCount' not in statistics
        duration_str = content_details.get('duration')
        duration_seconds = parse_duration(duration_str) if duration_str else 0

        # --- Channel Stats from Fetched Info ---
        channel_views = channel_info_dict.get('viewCount', 0)
        # Use videoCount from channel_info_dict directly
        channel_video_count = channel_info_dict.get('videoCount', 0)

        # --- Combined Metric ---
        avg_views_per_video = float(channel_views) / float(channel_video_count) if channel_video_count > 0 else 0
        # Fallback if avg is zero (e.g., new channel) but video has views
        if avg_views_per_video <= 0 and views > 0:
             avg_views_per_video = float(views) # Use current video views as a rough estimate

        combined_metric = float(views) / avg_views_per_video if avg_views_per_video > 0 else None

        # --- Determine URL (basic video vs shorts - simple duration check) ---
        item_type = 'shorts' if duration_seconds <= 60 else 'video' # Simple check
        if item_type == 'video':
            video_url = f'https://www.youtube.com/watch?v={video_id}'
        else: # shorts
            video_url = f'https://www.youtube.com/shorts/{video_id}'

        # --- Create Item ---
        item_obj = Item.model_validate({
            'video_id': video_id,
            'title': snippet.get('title', 'No Title'),
            'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
            'published_at': snippet.get('publishedAt'), # Pydantic handles parsing
            'views': views,
            'channel_title': channel_info_dict.get('channel_title', 'Unknown Channel'),
            'channel_url': channel_info_dict.get('channel_url', f'https://www.youtube.com/channel/{channel_id}'),
            'channel_subscribers': channel_info_dict.get('channel_subscribers', 0),
            'video_count': channel_video_count, # Total videos on channel
            'likes': likes,
            'likes_hidden': likes_hidden,
            'comments': comments,
            'comments_hidden': comments_hidden,
            'combined_metric': combined_metric,
            'duration': duration_seconds,
            'video_url': video_url,
            'channel_thumbnail': channel_info_dict.get('channel_thumbnail', ''),
        })
        logger.debug(f"Successfully built item for video_id: {video_id}")
        return item_obj

    except KeyError as e:
        logger.error(f"KeyError building item for video ID {video_id}: Missing key {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error building item for video ID {video_id}: {e}", exc_info=True)
        return None


# --- Endpoint 1: Get Info by Video IDs (remains the same) ---
@router.post("/videos_by_ids", response_model=SearchResponse, tags=["info"])
async def get_videos_by_ids(
    video_ids: List[str] = Body(..., embed=True, description="A list of YouTube video IDs (max 50)."),
    youtube: build = Depends(get_user_youtube_client_via_cookie)
):
    """
    Retrieves detailed information for a list of specified video IDs.
    Response structure matches the `/search/videos` endpoint.
    Requires authentication.
    """
    if not video_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Video ID list cannot be empty.")
    if len(video_ids) > 50:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum of 50 video IDs allowed per request.")

    logger.info(f"Request received for video IDs: {video_ids}")
    unique_video_ids = list(set(video_ids)) # Ensure unique IDs
    ids_string = ','.join(unique_video_ids)

    results: List[Item] = []
    channel_info_cache: Dict[str, Optional[Dict]] = {} # Cache channel info during this request

    try:
        # --- Fetch Video Details ---
        logger.info(f"Calling YouTube API: videos().list for IDs: {ids_string}")
        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=ids_string,
            maxResults=len(unique_video_ids)
        ).execute()

        video_items = video_response.get('items', [])
        logger.info(f"Received details for {len(video_items)} videos from API.")

        if not video_items:
             # Return empty list if none of the IDs were valid or found
             return SearchResponse(item_count=0, type='videos', items=[])

        # --- Process Each Video ---
        for video_detail in video_items:
             item = await build_item_from_video_details(youtube, video_detail, channel_info_cache)
             if item:
                 results.append(item)

        logger.info(f"Successfully processed {len(results)} videos.")

        return SearchResponse(item_count=len(results), type='videos', items=results)

    except HTTPException as he:
        # Re-raise HTTP exceptions from dependencies or helpers
        raise he
    except Exception as e:
        logger.error(f"Error fetching videos by IDs: {e}", exc_info=True)
        if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
             raise HTTPException(status_code=429, detail="YouTube API quota exceeded for user.")
        elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
             raise HTTPException(status_code=401, detail="YouTube API authorization error. Please re-login.")
        else:
             raise HTTPException(status_code=500, detail=f"Internal server error fetching video details: {e}")


# --- Endpoint 2: Get Latest Videos by Channel ID (Query Parameter) ---
# --- CHANGE: Path changed, channel_id moved to Query parameter ---
# @router.get("/channel_latest_videos", response_model=SearchResponse, tags=["info"])
# async def get_channel_latest_videos(
#     # --- CHANGE: channel_id is now a query parameter ---
#     channel_id: str = Query(..., description="The YouTube channel ID."),
#     youtube: build = Depends(get_user_youtube_client_via_cookie)
# ):
#     """
#     Retrieves the 6 most recent videos from the specified channel ID (provided as a query parameter).
#     Response structure matches the `/search/videos` endpoint.
#     Requires authentication.
#     """
#     # --- CHANGE: Logging reflects query parameter usage ---
#     logger.info(f"Request received for latest 6 videos from channel ID (query param): {channel_id}")
#
#     results: List[Item] = []
#     channel_info_cache: Dict[str, Optional[Dict]] = {} # Cache for this request
#
#     try:
#         # --- Step 1: Search for the latest 6 videos ---
#         logger.info(f"Calling YouTube API: search().list for channel {channel_id}")
#         search_response = youtube.search().list(
#             part='snippet',
#             channelId=channel_id,
#             order='date', # Order by date (most recent first)
#             type='video', # Ensure we get videos
#             maxResults=6
#         ).execute()
#
#         search_items = search_response.get('items', [])
#         logger.info(f"Found {len(search_items)} potential latest videos via search.")
#
#         if not search_items:
#             logger.info(f"No videos found for channel {channel_id}.")
#             return SearchResponse(item_count=0, type='videos', items=[])
#
#         video_ids = [item['id']['videoId'] for item in search_items if item.get('id', {}).get('videoId')]
#
#         if not video_ids:
#              logger.warning(f"Search results found, but no video IDs extracted for channel {channel_id}.")
#              return SearchResponse(item_count=0, type='videos', items=[])
#
#         ids_string = ','.join(video_ids)
#
#         # --- Step 2: Get details for these specific videos ---
#         logger.info(f"Calling YouTube API: videos().list for latest video IDs: {ids_string}")
#         video_response = youtube.videos().list(
#             part="snippet,contentDetails,statistics",
#             id=ids_string,
#             maxResults=len(video_ids)
#         ).execute()
#
#         video_items = video_response.get('items', [])
#         logger.info(f"Received details for {len(video_items)} latest videos from API.")
#
#         if not video_items:
#              logger.warning(f"Could not get details for the found video IDs: {ids_string}")
#              return SearchResponse(item_count=0, type='videos', items=[])
#
#         # --- Step 3: Process Each Video (using a pre-fetched channel info) ---
#         # Fetch channel info ONCE using the input channel_id
#         channel_info_dict = await get_channel_info(youtube, channel_id)
#         if not channel_info_dict:
#              logger.error(f"Failed to get channel info for the primary channel ID: {channel_id}. Cannot proceed.")
#              raise HTTPException(status_code=404, detail=f"Channel info not found for ID: {channel_id}")
#
#         channel_info_cache[channel_id] = channel_info_dict # Pre-populate cache
#
#         for video_detail in video_items:
#             item = await build_item_from_video_details(youtube, video_detail, channel_info_cache)
#             if item:
#                 results.append(item)
#
#         logger.info(f"Successfully processed {len(results)} latest videos for channel {channel_id}.")
#
#         return SearchResponse(item_count=len(results), type='videos', items=results)
#
#     except HTTPException as he:
#         # Re-raise HTTP exceptions
#         raise he
#     except Exception as e:
#         logger.error(f"Error fetching latest channel videos for {channel_id}: {e}", exc_info=True)
#         # Error handling remains largely the same, but messages reflect it's a query param issue if relevant
#         if 'HttpError 404' in str(e) and 'channelNotFound' in str(e):
#              raise HTTPException(status_code=404, detail=f"Channel not found for ID provided in query: {channel_id}")
#         elif 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
#              raise HTTPException(status_code=429, detail="YouTube API quota exceeded for user.")
#         elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
#              raise HTTPException(status_code=401, detail="YouTube API authorization error. Please re-login.")
#         else:
#              raise HTTPException(status_code=500, detail=f"Internal server error fetching latest channel videos: {e}")