# Import all SQLAlchemy models here so Alembic and the app can discover them.
from backend.models.user import User, UserPermission  # noqa: F401
from backend.models.book import Book, BookFile, BookTag  # noqa: F401
from backend.models.library import Library, SavedFilter, BookType  # noqa: F401
from backend.models.user_book_status import UserBookStatus  # noqa: F401
from backend.models.user_series_rating import UserSeriesRating  # noqa: F401
from backend.models.api_token import ApiToken  # noqa: F401
from backend.models.series_meta import Arc, SeriesMeta  # noqa: F401
from backend.models.user_device import UserDevice  # noqa: F401
from backend.models.wish import Wish  # noqa: F401
from backend.models.notification import Notification  # noqa: F401
from backend.models.send_queue import SendQueueItem  # noqa: F401
from backend.models.user_dashboard import UserDashboard  # noqa: F401
