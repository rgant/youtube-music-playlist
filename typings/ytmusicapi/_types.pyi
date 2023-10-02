from typing import Literal, TypedDict

Identifier = TypedDict(
    'Identifier',
    {
        'id': str,
        'name': str,
    },
)

LikeStatus = Literal['INDIFFERENT', 'LIKE', 'DISLIKE']

Song = TypedDict(
    'Song',
    {
        'album': Identifier,
        'artists': list[Identifier],
        'duration': str,
        'duration_seconds': int,
        'isAvailable': bool,
        'isExplicit': bool,
        'likeStatus': LikeStatus,
        'thumbnails': list[Thumbnail],
        'title': str,
        'videoId': str,
        'videoType': str,
    },
)

Thumbnail = TypedDict(
    'Thumbnail',
    {
        'height': int,
        'url': str,
        'width': int,
    },
)
