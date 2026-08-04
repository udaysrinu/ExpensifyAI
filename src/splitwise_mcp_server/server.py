"""FastMCP server implementation with tool definitions."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, AsyncIterator
from fastmcp import FastMCP

from .config import SplitwiseConfig
from .auth import OAuth2Handler, APIKeyHandler
from .client import SplitwiseClient
from .resolver import EntityResolver
from . import analytics as analytics_mod
from . import dashboard as dashboard_mod
from . import itemize as itemize_mod
from . import splits_store
from . import mirror as mirror_mod
from . import statement_import as statement_import_mod
from .errors import (
    ValidationError,
    RateLimitError,
    validate_required,
    validate_positive_number,
    validate_currency_code,
    validate_date_format,
    validate_email,
    validate_range,
    validate_choice,
    validate_user_split
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global instances (initialized in lifespan)
client: Optional[SplitwiseClient] = None
resolver: Optional[EntityResolver] = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Lifespan context manager for server startup and shutdown.
    
    This function handles initialization and cleanup of resources that should
    persist for the lifetime of the server, not per-session.
    
    Args:
        server: The FastMCP server instance
        
    Yields:
        None
    """
    global client, resolver
    
    # Startup: Initialize resources
    logger.info("Starting Splitwise MCP Server...")
    
    # Load configuration from environment
    config = SplitwiseConfig.from_env()
    
    # Set up logging level
    logging.getLogger().setLevel(config.log_level)
    
    # Initialize authentication handler
    if config.has_oauth():
        logger.info("Using OAuth2 authentication")
        auth_handler = OAuth2Handler(
            consumer_key=config.oauth_consumer_key,
            consumer_secret=config.oauth_consumer_secret,
            access_token=config.oauth_access_token
        )
    elif config.has_api_key():
        logger.info("Using API Key authentication")
        auth_handler = APIKeyHandler(api_key=config.api_key)
    else:
        raise ValueError("No valid authentication method configured")
    
    # Initialize SplitwiseClient
    client = SplitwiseClient(auth_handler, cache_ttl=config.cache_ttl_seconds)
    logger.info("SplitwiseClient initialized")
    
    # Initialize EntityResolver
    resolver = EntityResolver(client)
    resolver.default_threshold = config.default_match_threshold
    logger.info("EntityResolver initialized")
    
    logger.info("Splitwise MCP Server started successfully")
    
    try:
        yield
    finally:
        # Shutdown: Cleanup resources
        logger.info("Shutting down Splitwise MCP Server...")
        if client:
            await client.close()
            logger.info("SplitwiseClient closed")
        logger.info("Splitwise MCP Server shutdown complete")


def create_server() -> FastMCP:
    """Create and configure the FastMCP server instance.
    
    This function creates the FastMCP server with all Splitwise tools and
    configures the lifespan for proper resource management.
    
    Returns:
        Configured FastMCP server instance
        
    Raises:
        ValueError: If authentication configuration is invalid
    """
    # Create FastMCP server with lifespan
    mcp = FastMCP("Splitwise MCP Server", lifespan=lifespan)
    logger.info("FastMCP server created")
    
    # Register all tools
    register_user_tools(mcp)
    register_expense_tools(mcp)
    register_group_tools(mcp)
    register_friend_tools(mcp)
    register_resolution_tools(mcp)
    register_comment_tools(mcp)
    register_notification_tools(mcp)
    register_utility_tools(mcp)
    register_analytics_tools(mcp)
    register_itemization_tools(mcp)
    register_sync_tools(mcp)
    register_statement_tools(mcp)

    logger.info("All tools registered successfully")
    
    return mcp



# ============================================================================
# User Tools
# ============================================================================

def register_user_tools(mcp: FastMCP) -> None:
    """Register user-related MCP tools."""
    
    @mcp.tool()
    async def get_current_user() -> Dict[str, Any]:
        """Get the current authenticated user's profile (id, name, email, picture)."""
        try:
            result = await client.get_current_user()
            logger.info("Retrieved current user information")
            return result
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            raise
    
    @mcp.tool()
    async def get_user(user_id: int) -> Dict[str, Any]:
        """Get a user's profile by their ID."""
        try:
            result = await client.get_user(user_id)
            logger.info(f"Retrieved user information for user_id={user_id}")
            return result
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            raise


# ============================================================================
# Expense Tools
# ============================================================================

def register_expense_tools(mcp: FastMCP) -> None:
    """Register expense-related MCP tools."""
    
    @mcp.tool()
    async def create_expense(
        cost: str,
        description: str,
        group_id: int = 0,
        currency_code: str = "USD",
        date: Optional[str] = None,
        category_id: Optional[int] = None,
        details: Optional[str] = None,
        repeat_interval: Optional[str] = None,
        users: Optional[List[Dict[str, Any]]] = None,
        split_equally: bool = True
    ) -> Dict[str, Any]:
        """Create a new expense. Cost is a string with 2 decimals (e.g. "25.50").
        Splits equally by default; provide users list with paid_share/owed_share for custom splits.
        Each user needs user_id or (email + first_name + last_name).
        Set repeat_interval to "weekly", "fortnightly", "monthly", or "yearly" for recurring expenses.
        """
        try:
            validate_required(cost, "cost")
            validate_required(description, "description")
            validate_positive_number(cost, "cost")
            validate_currency_code(currency_code)

            if date:
                validate_date_format(date, "date")
            if group_id < 0:
                raise ValidationError(
                    "group_id must be non-negative (use 0 for non-group expenses)",
                    field="group_id",
                    details={"value": group_id}
                )
            if category_id is not None and category_id <= 0:
                raise ValidationError(
                    "category_id must be a positive integer",
                    field="category_id",
                    details={"value": category_id}
                )
            if repeat_interval is not None:
                valid_intervals = ["never", "weekly", "fortnightly", "monthly", "yearly"]
                validate_choice(repeat_interval, "repeat_interval", valid_intervals)
            if users:
                validate_user_split(users)

            expense_data = {
                "cost": cost,
                "description": description,
                "currency_code": currency_code,
                "group_id": group_id,
            }

            if users:
                expense_data["split_equally"] = False
                expense_data["users"] = users
            elif split_equally:
                expense_data["split_equally"] = True

            if date:
                expense_data["date"] = date
            else:
                expense_data["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if category_id is not None:
                expense_data["category_id"] = category_id
            if details is not None:
                expense_data["details"] = details
            if repeat_interval is not None:
                expense_data["repeat_interval"] = repeat_interval

            result = await client.create_expense(expense_data)
            logger.info(f"Created expense: {description} (${cost})")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating expense: {e}")
            raise
    
    @mcp.tool()
    async def get_expenses(
        group_id: Optional[int] = None,
        friend_id: Optional[int] = None,
        dated_after: Optional[str] = None,
        dated_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List expenses with optional filters. Dates are ISO 8601 format. Max limit is 100."""
        try:
            # Validate date formats if provided
            if dated_after:
                validate_date_format(dated_after, "dated_after")
            if dated_before:
                validate_date_format(dated_before, "dated_before")
            if updated_after:
                validate_date_format(updated_after, "updated_after")
            if updated_before:
                validate_date_format(updated_before, "updated_before")
            
            # Validate pagination parameters
            validate_range(limit, "limit", min_val=1, max_val=100)
            validate_range(offset, "offset", min_val=0)
            
            result = await client.get_expenses(
                group_id=group_id,
                friend_id=friend_id,
                dated_after=dated_after,
                dated_before=dated_before,
                updated_after=updated_after,
                updated_before=updated_before,
                limit=limit,
                offset=offset
            )
            logger.info(f"Retrieved expenses (limit={limit}, offset={offset})")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error getting expenses: {e}")
            raise
    
    @mcp.tool()
    async def get_expense(expense_id: int) -> Dict[str, Any]:
        """Get full details of a single expense including users, splits, and comments."""
        try:
            result = await client.get_expense(expense_id)
            logger.info(f"Retrieved expense {expense_id}")
            return result
        except Exception as e:
            logger.error(f"Error getting expense {expense_id}: {e}")
            raise
    
    @mcp.tool()
    async def update_expense(
        expense_id: int,
        cost: Optional[str] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        category_id: Optional[int] = None,
        currency_code: Optional[str] = None,
        group_id: Optional[int] = None,
        details: Optional[str] = None,
        repeat_interval: Optional[str] = None,
        users: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Update an existing expense. Only provided fields are changed.
        If any users are supplied, all shares for the expense are overwritten with the provided values.
        """
        try:
            validate_required(expense_id, "expense_id")
            if expense_id <= 0:
                raise ValidationError(
                    "expense_id must be a positive integer",
                    field="expense_id",
                    details={"value": expense_id}
                )
            if cost is not None:
                validate_positive_number(cost, "cost")
            if date is not None:
                validate_date_format(date, "date")
            if category_id is not None and category_id <= 0:
                raise ValidationError(
                    "category_id must be a positive integer",
                    field="category_id",
                    details={"value": category_id}
                )
            if currency_code is not None:
                validate_currency_code(currency_code)
            if group_id is not None and group_id < 0:
                raise ValidationError(
                    "group_id must be non-negative",
                    field="group_id",
                    details={"value": group_id}
                )
            if repeat_interval is not None:
                valid_intervals = ["never", "weekly", "fortnightly", "monthly", "yearly"]
                validate_choice(repeat_interval, "repeat_interval", valid_intervals)
            if users is not None:
                validate_user_split(users)

            expense_data = {}
            if cost is not None:
                expense_data["cost"] = cost
            if description is not None:
                expense_data["description"] = description
            if date is not None:
                expense_data["date"] = date
            if category_id is not None:
                expense_data["category_id"] = category_id
            if currency_code is not None:
                expense_data["currency_code"] = currency_code
            if group_id is not None:
                expense_data["group_id"] = group_id
            if details is not None:
                expense_data["details"] = details
            if repeat_interval is not None:
                expense_data["repeat_interval"] = repeat_interval
            if users is not None:
                expense_data["users"] = users

            if not expense_data:
                raise ValidationError(
                    "At least one field must be provided to update",
                    details={"provided_fields": []}
                )

            result = await client.update_expense(expense_id, expense_data)
            logger.info(f"Updated expense {expense_id}")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error updating expense {expense_id}: {e}")
            raise
    
    @mcp.tool()
    async def delete_expense(expense_id: int) -> Dict[str, Any]:
        """Delete an expense permanently. Use restore_expense to undo."""
        try:
            result = await client.delete_expense(expense_id)
            logger.info(f"Deleted expense {expense_id}")
            return result
        except Exception as e:
            logger.error(f"Error deleting expense {expense_id}: {e}")
            raise

    @mcp.tool()
    async def restore_expense(expense_id: int) -> Dict[str, Any]:
        """Restore a previously deleted expense. Use this to undo an accidental deletion."""
        try:
            result = await client.restore_expense(expense_id)
            logger.info(f"Restored expense {expense_id}")
            return result
        except Exception as e:
            logger.error(f"Error restoring expense {expense_id}: {e}")
            raise


# ============================================================================
# Group Tools
# ============================================================================

def register_group_tools(mcp: FastMCP) -> None:
    """Register group-related MCP tools."""
    
    @mcp.tool()
    async def get_groups() -> Dict[str, Any]:
        """List all groups the current user belongs to, with members and balances."""
        try:
            result = await client.get_groups()
            logger.info("Retrieved groups list")
            return result
        except Exception as e:
            logger.error(f"Error getting groups: {e}")
            raise
    
    @mcp.tool()
    async def get_group(group_id: int) -> Dict[str, Any]:
        """Get a group's details including members, balances, and simplified debts."""
        try:
            result = await client.get_group(group_id)
            logger.info(f"Retrieved group {group_id}")
            return result
        except Exception as e:
            logger.error(f"Error getting group {group_id}: {e}")
            raise
    
    @mcp.tool()
    async def create_group(
        name: str,
        group_type: str = "other",
        simplify_by_default: bool = True,
        users: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Create a new group. group_type is one of: home, trip, couple, other.
        Optionally add initial members via users list with user_id or email+name."""
        try:
            # Validate required parameters
            validate_required(name, "name")
            
            # Validate group_type
            valid_types = ["home", "trip", "couple", "other"]
            validate_choice(group_type, "group_type", valid_types)
            
            # Validate users list if provided
            if users:
                if not isinstance(users, list):
                    raise ValidationError(
                        "users must be a list",
                        field="users",
                        details={"type": type(users).__name__}
                    )
                
                for i, user in enumerate(users):
                    if not isinstance(user, dict):
                        raise ValidationError(
                            f"users[{i}] must be a dictionary",
                            field="users",
                            details={"index": i, "type": type(user).__name__}
                        )
                    
                    # Validate email if provided
                    if "email" in user and user["email"]:
                        validate_email(user["email"])
            
            group_data = {
                "name": name,
                "group_type": group_type,
                "simplify_by_default": simplify_by_default
            }
            
            if users:
                group_data["users"] = users
            
            result = await client.create_group(group_data)
            logger.info(f"Created group: {name}")
            
            # Clear resolver cache since groups list changed
            resolver.clear_cache()
            
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating group: {e}")
            raise
    
    @mcp.tool()
    async def delete_group(group_id: int) -> Dict[str, Any]:
        """Delete a group. All expenses must be settled first."""
        try:
            result = await client.delete_group(group_id)
            logger.info(f"Deleted group {group_id}")
            
            # Clear resolver cache since groups list changed
            resolver.clear_cache()
            
            return result
        except Exception as e:
            logger.error(f"Error deleting group {group_id}: {e}")
            raise
    
    @mcp.tool()
    async def add_user_to_group(
        group_id: int,
        user_id: Optional[int] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add a user to a group by user_id or by email (with first_name/last_name for new invites)."""
        try:
            # Validate group_id
            validate_required(group_id, "group_id")
            if group_id <= 0:
                raise ValidationError(
                    "group_id must be a positive integer",
                    field="group_id",
                    details={"value": group_id}
                )
            
            # Validate that either user_id or email is provided
            if not user_id and not email:
                raise ValidationError(
                    "Either user_id or email must be provided",
                    details={"user_id": user_id, "email": email}
                )
            
            # Validate user_id if provided
            if user_id is not None and user_id <= 0:
                raise ValidationError(
                    "user_id must be a positive integer",
                    field="user_id",
                    details={"value": user_id}
                )
            
            # Validate email if provided
            if email:
                validate_email(email)
            
            user_data = {}
            if user_id is not None:
                user_data["user_id"] = user_id
            if email:
                user_data["email"] = email
            if first_name:
                user_data["first_name"] = first_name
            if last_name:
                user_data["last_name"] = last_name
            
            result = await client.add_user_to_group(group_id, user_data)
            logger.info(f"Added user to group {group_id}")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error adding user to group {group_id}: {e}")
            raise
    
    @mcp.tool()
    async def remove_user_from_group(group_id: int, user_id: int) -> Dict[str, Any]:
        """Remove a user from a group. User must have zero balance in the group."""
        try:
            result = await client.remove_user_from_group(group_id, user_id)
            logger.info(f"Removed user {user_id} from group {group_id}")
            return result
        except Exception as e:
            logger.error(f"Error removing user {user_id} from group {group_id}: {e}")
            raise


# ============================================================================
# Friend Tools
# ============================================================================

def register_friend_tools(mcp: FastMCP) -> None:
    """Register friend-related MCP tools."""
    
    @mcp.tool()
    async def get_friends() -> Dict[str, Any]:
        """List all friends with their balance information."""
        try:
            result = await client.get_friends()
            logger.info("Retrieved friends list")
            return result
        except Exception as e:
            logger.error(f"Error getting friends: {e}")
            raise
    
    @mcp.tool()
    async def get_friend(user_id: int) -> Dict[str, Any]:
        """Get a friend's details including balances and shared groups."""
        try:
            result = await client.get_friend(user_id)
            logger.info(f"Retrieved friend {user_id}")
            return result
        except Exception as e:
            logger.error(f"Error getting friend {user_id}: {e}")
            raise

    @mcp.tool()
    async def create_friend(user_email: str, user_first_name: str = "", user_last_name: str = "") -> Dict[str, Any]:
        """Add a friend by email address. Optionally provide their first and last name."""
        try:
            validate_required(user_email, "user_email")
            validate_email(user_email)
            result = await client.create_friend(user_email, user_first_name, user_last_name)
            resolver.clear_cache()
            logger.info(f"Created friend: {user_email}")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating friend: {e}")
            raise

    @mcp.tool()
    async def delete_friend(friend_id: int) -> Dict[str, Any]:
        """Remove a friendship. Does not affect shared expenses or balances."""
        try:
            result = await client.delete_friend(friend_id)
            resolver.clear_cache()
            logger.info(f"Deleted friend {friend_id}")
            return result
        except Exception as e:
            logger.error(f"Error deleting friend {friend_id}: {e}")
            raise


# ============================================================================
# Resolution Tools
# ============================================================================

def register_resolution_tools(mcp: FastMCP) -> None:
    """Register entity resolution MCP tools."""
    
    @mcp.tool()
    async def resolve_friend(query: str, threshold: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fuzzy-match a friend by name. Returns matches with id, name, and match_score.
        Use this when you know a name but not the user_id."""
        try:
            validate_required(query, "query")
            effective_threshold = threshold if threshold is not None else resolver.default_threshold
            validate_range(effective_threshold, "threshold", min_val=0, max_val=100)

            matches = await resolver.resolve_friend(query, effective_threshold)
            result = [
                {
                    "id": match.id,
                    "name": match.name,
                    "match_score": match.match_score,
                    "additional_info": match.additional_info
                }
                for match in matches
            ]
            logger.info(f"Resolved friend '{query}': found {len(result)} matches")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving friend '{query}': {e}")
            raise
    
    @mcp.tool()
    async def resolve_group(query: str, threshold: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fuzzy-match a group by name. Returns matches with id, name, and match_score.
        Use this when you know a group name but not the group_id."""
        try:
            validate_required(query, "query")
            effective_threshold = threshold if threshold is not None else resolver.default_threshold
            validate_range(effective_threshold, "threshold", min_val=0, max_val=100)

            matches = await resolver.resolve_group(query, effective_threshold)
            result = [
                {
                    "id": match.id,
                    "name": match.name,
                    "match_score": match.match_score,
                    "additional_info": match.additional_info
                }
                for match in matches
            ]
            logger.info(f"Resolved group '{query}': found {len(result)} matches")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving group '{query}': {e}")
            raise
    
    @mcp.tool()
    async def resolve_category(query: str, threshold: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fuzzy-match an expense category by name (e.g. "food", "utilities").
        Returns matches with id, name, and match_score. Searches subcategories too."""
        try:
            validate_required(query, "query")
            effective_threshold = threshold if threshold is not None else resolver.default_threshold
            validate_range(effective_threshold, "threshold", min_val=0, max_val=100)

            matches = await resolver.resolve_category(query, effective_threshold)
            result = [
                {
                    "id": match.id,
                    "name": match.name,
                    "match_score": match.match_score,
                    "additional_info": match.additional_info
                }
                for match in matches
            ]
            logger.info(f"Resolved category '{query}': found {len(result)} matches")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving category '{query}': {e}")
            raise


# ============================================================================
# Comment Tools
# ============================================================================

def register_comment_tools(mcp: FastMCP) -> None:
    """Register comment-related MCP tools."""
    
    @mcp.tool()
    async def create_comment(expense_id: int, content: str) -> Dict[str, Any]:
        """Add a comment to an expense. Visible to all users in the expense."""
        try:
            # Validate expense_id
            validate_required(expense_id, "expense_id")
            if expense_id <= 0:
                raise ValidationError(
                    "expense_id must be a positive integer",
                    field="expense_id",
                    details={"value": expense_id}
                )
            
            # Validate content
            validate_required(content, "content")
            
            result = await client.create_comment(expense_id, content)
            logger.info(f"Created comment on expense {expense_id}")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating comment on expense {expense_id}: {e}")
            raise
    
    @mcp.tool()
    async def get_comments(expense_id: int) -> Dict[str, Any]:
        """Get all comments on an expense."""
        try:
            result = await client.get_comments(expense_id)
            logger.info(f"Retrieved comments for expense {expense_id}")
            return result
        except Exception as e:
            logger.error(f"Error getting comments for expense {expense_id}: {e}")
            raise
    
    @mcp.tool()
    async def delete_comment(comment_id: int) -> Dict[str, Any]:
        """Delete a comment. You can only delete your own comments."""
        try:
            result = await client.delete_comment(comment_id)
            logger.info(f"Deleted comment {comment_id}")
            return result
        except Exception as e:
            logger.error(f"Error deleting comment {comment_id}: {e}")
            raise


# ============================================================================
# Notification Tools
# ============================================================================

def register_notification_tools(mcp: FastMCP) -> None:
    """Register notification-related MCP tools."""

    @mcp.tool()
    async def get_notifications() -> Dict[str, Any]:
        """Get recent notifications for the current user (new expenses, payments, comments, group activity)."""
        try:
            result = await client.get_notifications()
            logger.info("Retrieved notifications")
            return result
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            raise


# ============================================================================
# Utility Tools
# ============================================================================

def register_utility_tools(mcp: FastMCP) -> None:
    """Register utility MCP tools."""
    
    @mcp.tool()
    async def get_categories() -> Dict[str, Any]:
        """Get all expense categories and subcategories. Results are cached."""
        try:
            result = await client.get_categories()
            logger.info("Retrieved categories")
            return result
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            raise
    
    @mcp.tool()
    async def get_currencies() -> Dict[str, Any]:
        """Get all supported currency codes and symbols. Results are cached."""
        try:
            result = await client.get_currencies()
            logger.info("Retrieved currencies")
            return result
        except Exception as e:
            logger.error(f"Error getting currencies: {e}")
            raise


# ============================================================================
# Analytics Tools
# ============================================================================

async def _fetch_all_expenses(
    group_id: Optional[int] = None,
    friend_id: Optional[int] = None,
    dated_after: Optional[str] = None,
    dated_before: Optional[str] = None,
    max_pages: int = 50,
) -> Dict[str, Any]:
    """Fetch every matching expense by paginating sequentially (100/page).

    Sequential (never bursty) to stay under Splitwise's unpublished rate limit;
    the client already honors 429 retry_after. Stops when a short page returns.
    If max_pages is hit, flags truncated=True (logged) rather than silently
    dropping transactions.
    """
    page_size = 100
    all_expenses: List[Dict[str, Any]] = []
    pages = 0
    truncated = False
    for page in range(max_pages):
        pages += 1
        resp = await client.get_expenses(
            group_id=group_id,
            friend_id=friend_id,
            dated_after=dated_after,
            dated_before=dated_before,
            limit=page_size,
            offset=page * page_size,
        )
        batch = resp.get("expenses", []) if isinstance(resp, dict) else []
        all_expenses.extend(batch)
        if len(batch) < page_size:
            break
    else:
        truncated = True
        logger.warning(f"Expense fetch hit max_pages={max_pages}; result truncated.")
    return {"expenses": all_expenses, "pages_fetched": pages, "truncated": truncated}


def register_analytics_tools(mcp: FastMCP) -> None:
    """Register deterministic analytics tools (computed in Python, not by the LLM)."""

    @mcp.tool()
    async def analyze_spending(
        target_type: str = "me",
        target_id: Optional[int] = None,
        dated_after: Optional[str] = None,
        dated_before: Optional[str] = None,
        generate_dashboard: bool = False,
        top_n: int = 10,
    ) -> Dict[str, Any]:
        """Deterministic spending analytics for the current user, a group, or a friend.

        All numbers are computed in Python (never estimated by the model): category
        breakdown, monthly trend, owed-vs-paid ("mine vs split"), transaction ledger,
        top transactions, and — for groups — per-member comparison, category×member
        matrix, and a minimum-transaction settlement plan. Every result includes a
        reconciliation check (shares must sum to cost) and a multi-currency guard.

        target_type: "me" (all your expenses), "group" (needs target_id=group_id),
                     or "friend" (needs target_id=friend user_id).
        dated_after / dated_before: ISO 8601 date filters (optional).
        generate_dashboard: if True, also writes a self-contained HTML dashboard and
                            returns its file path in `dashboard_path`.
        Perspective is always the authenticated user.
        """
        try:
            validate_choice(target_type, "target_type", ["me", "group", "friend"])
            if dated_after:
                validate_date_format(dated_after, "dated_after")
            if dated_before:
                validate_date_format(dated_before, "dated_before")
            if target_type in ("group", "friend") and target_id is None:
                raise ValidationError(
                    f"target_id is required when target_type is '{target_type}'",
                    field="target_id",
                )

            me = (await client.get_current_user())["user"]
            current_user_id = me["id"]

            label = "My spending"
            group_id = friend_id = None
            if target_type == "group":
                group_id = target_id
                try:
                    grp = (await client.get_group(target_id))["group"]
                    label = grp.get("name", f"Group {target_id}")
                except Exception:
                    label = f"Group {target_id}"
            elif target_type == "friend":
                friend_id = target_id
                try:
                    fr = (await client.get_friend(target_id))["friend"]
                    label = f"{fr.get('first_name','')} {fr.get('last_name','') or ''}".strip()
                except Exception:
                    label = f"Friend {target_id}"

            fetched = await _fetch_all_expenses(
                group_id=group_id, friend_id=friend_id,
                dated_after=dated_after, dated_before=dated_before,
            )

            result = analytics_mod.compute_analytics(
                fetched["expenses"],
                current_user_id=current_user_id,
                target_type=target_type,
                target_id=target_id,
                target_label=label,
                top_n=top_n,
                truncated=fetched["truncated"],
                pages_fetched=fetched["pages_fetched"],
            )

            if generate_dashboard:
                try:
                    dataset = analytics_mod.build_dataset(
                        fetched["expenses"],
                        current_user_id=current_user_id,
                        target_type=target_type,
                        target_id=target_id,
                        target_label=label,
                        truncated=fetched["truncated"],
                        pages_fetched=fetched["pages_fetched"],
                    )
                    path = dashboard_mod.write_dashboard(dataset)
                    result["dashboard_path"] = path
                except Exception as e:
                    logger.error(f"Dashboard write failed: {e}")
                    result["dashboard_error"] = str(e)

            logger.info(f"Analyzed {target_type} '{label}': {result['meta']['expense_count']} expenses")
            return result
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error analyzing spending: {e}")
            raise

    @mcp.tool()
    async def compare_group_members(
        group_id: int,
        dated_after: Optional[str] = None,
        dated_before: Optional[str] = None,
        generate_dashboard: bool = False,
    ) -> Dict[str, Any]:
        """Deterministic per-member comparison for a group: total spend + ranking,
        category×member matrix, insights (highest/lowest/average/spread), and a
        minimum-transaction settlement plan. Convenience wrapper over analyze_spending
        with target_type='group'.
        """
        return await analyze_spending(
            target_type="group",
            target_id=group_id,
            dated_after=dated_after,
            dated_before=dated_before,
            generate_dashboard=generate_dashboard,
        )


# ============================================================================
# Itemization Tools (structured line-items, per-item splits, default templates)
# ============================================================================

def register_itemization_tools(mcp: FastMCP) -> None:
    """Register itemized-expense + default-split tools.

    Receipt scanning is LLM-vision-native: the calling agent reads the receipt image,
    extracts line-items, then calls create_itemized_expense. This module owns the exact
    (integer-paise) split math and the Splitwise write — not OCR.
    """

    @mcp.tool()
    async def create_itemized_expense(
        description: str,
        group_id: int,
        items: List[Dict[str, Any]],
        currency_code: str = "INR",
        date: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Create ONE Splitwise expense from itemized line-items, each with its OWN split.

        This is how a receipt becomes an expense: the agent extracts line-items from the
        receipt image and passes them here. Each item can split differently (e.g. beers
        3/4 to one person, groceries 4-way, cake between two) — the tool computes each
        person's total owed_share exactly in integer paise and reconciles to the total
        before writing. Set dry_run=True to preview the computed split without creating.

        items: list of
          {
            "desc": "Beers",
            "amount": "2710.00",           # rupees, string with 2 decimals
            "category": "Drinks",          # optional (free text, informational)
            "paid_by": <user_id>,          # who fronted this item
            "split": {                     # OR "split_ref": "<saved template name>"
               "type": "equal" | "shares" | "exact",
               "among": [user_id, ...],           # for equal/shares
               "shares": {user_id: weight, ...},  # for shares
               "exact":  {user_id: "amount", ...} # for exact (must sum to amount)
            }
          }
        """
        try:
            validate_required(description, "description")
            if date:
                validate_date_format(date, "date")
            if not items:
                raise ValidationError("items is required and must be non-empty", field="items")

            templates = splits_store.load_all()
            try:
                agg = itemize_mod.aggregate_items(items, default_splits=templates)
            except itemize_mod.ItemizeError as e:
                raise ValidationError(str(e), field="items")

            if not agg["reconciled"]:
                return {
                    "created": False,
                    "reconciled": False,
                    "discrepancy": agg["discrepancy"],
                    "message": ("Per-item shares do not sum to the item totals; refusing to "
                                "create a wrong expense. Check the line-items."),
                    "computed": {"cost": agg["cost"], "users": agg["users"]},
                }

            preview = {
                "created": False,
                "reconciled": True,
                "cost": agg["cost"],
                "currency_code": currency_code,
                "users": agg["users"],
                "per_user": {str(k): v for k, v in agg["per_user"].items()},
                "details": agg["details"],
            }
            if dry_run:
                preview["dry_run"] = True
                return preview

            expense_data = {
                "cost": agg["cost"],
                "description": description,
                "currency_code": currency_code,
                "group_id": group_id,
                "split_equally": False,
                "users": agg["users"],
                "details": agg["details"] + "\n\n— Automated note by ExpensifyAI "
                                            "(github.com/udaysrinu/ExpensifyAI). "
                                            "If you have any doubts, reach out to Uday.",
            }
            expense_data["date"] = date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            result = await client.create_expense(expense_data)
            errors = result.get("errors")
            has_err = bool(errors) and (errors if isinstance(errors, list) else errors.get("base"))
            created = result.get("expenses", [])
            logger.info(f"Itemized expense '{description}': {len(items)} items, cost {agg['cost']}")
            return {
                "created": not has_err and bool(created),
                "reconciled": True,
                "expense": created[0] if created else None,
                "errors": errors,
                "computed": {"cost": agg["cost"], "users": agg["users"]},
            }
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating itemized expense: {e}")
            raise

    @mcp.tool()
    async def save_default_split(name: str, split: Dict[str, Any]) -> Dict[str, Any]:
        """Save a reusable split template by name (e.g. "roomies-4way").

        split: {"type": "equal"|"shares", "among": [user_id, ...], "shares"?: {user_id: weight}}
        Referenced from create_itemized_expense items via "split_ref": "<name>".
        Stored locally in ~/.expensifyai/splits.json.
        """
        try:
            validate_required(name, "name")
            if not isinstance(split, dict) or "type" not in split:
                raise ValidationError("split must be an object with a 'type'", field="split")
            splits_store.save(name, split)
            return {"saved": True, "name": name, "split": split}
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error saving default split: {e}")
            raise

    @mcp.tool()
    async def list_default_splits() -> Dict[str, Any]:
        """List all saved split templates."""
        return {"splits": splits_store.load_all()}

    @mcp.tool()
    async def delete_default_split(name: str) -> Dict[str, Any]:
        """Delete a saved split template by name."""
        return {"deleted": splits_store.delete(name), "name": name}

    @mcp.tool()
    async def attach_receipt(expense_id: int, image_path: str) -> Dict[str, Any]:
        """Attach a receipt image or PDF (from a local file path) to an existing expense.

        Uploads the file to Splitwise via multipart on update_expense. Supports common
        image types and PDF. The receipt then shows on the expense in the app/website.
        (Splitwise's API only exposes receipt UPLOAD, not OCR — pair this with
        create_itemized_expense, where the calling agent reads the image for line-items.)
        """
        import os
        import mimetypes

        try:
            if not os.path.isfile(image_path):
                raise ValidationError(f"file not found: {image_path}", field="image_path")
            size = os.path.getsize(image_path)
            if size == 0:
                raise ValidationError("file is empty", field="image_path")
            if size > 25 * 1024 * 1024:
                raise ValidationError("file exceeds 25 MB", field="image_path")

            ctype = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            filename = os.path.basename(image_path)
            with open(image_path, "rb") as fh:
                content = fh.read()

            result = await client.post_multipart(
                f"/update_expense/{expense_id}",
                files={"receipt": (filename, content, ctype)},
            )
            errors = result.get("errors")
            has_err = bool(errors) and (errors if isinstance(errors, list) else errors.get("base"))
            expenses = result.get("expenses", [])
            receipt = expenses[0].get("receipt") if expenses else None
            logger.info(f"Attached receipt '{filename}' ({size} bytes) to expense {expense_id}")
            return {
                "attached": not has_err and bool(expenses),
                "expense_id": expense_id,
                "filename": filename,
                "receipt": receipt,
                "errors": errors,
            }
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error attaching receipt: {e}")
            raise

# ============================================================================
# Sync + Search Tools (local SQLite mirror, delta sync)
# ============================================================================

def register_sync_tools(mcp: FastMCP) -> None:
    """Register the local-mirror delta sync + search tools."""

    @mcp.tool()
    async def sync_all(full: bool = False) -> Dict[str, Any]:
        """Sync Splitwise into a local SQLite mirror for instant offline search.

        Delta sync: uses the API's `updated_after` cursor so only expenses that were
        added, edited, moved, or deleted since the last sync are fetched (first run, or
        full=True, pulls everything). Groups and friends are fully refreshed each run
        (small). Upserts by expense id, so re-running is safe. Returns counts.
        """
        try:
            conn = mirror_mod.connect()
            cursor = None if full else mirror_mod.get_cursor(conn)
            sync_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # refresh groups + friends (small, full each time)
            try:
                for g in (await client.get_groups()).get("groups", []):
                    mirror_mod.upsert_group(conn, g)
                for fr in (await client.get_friends()).get("friends", []):
                    mirror_mod.upsert_friend(conn, fr)
            except Exception as e:
                logger.warning(f"group/friend refresh failed: {e}")

            # delta (or full) expense pull, paginated sequentially
            counts = {"new": 0, "updated": 0, "deleted": 0, "skipped": 0}
            page_size, offset, pages = 100, 0, 0
            while pages < 200:
                pages += 1
                resp = await client.get_expenses(
                    updated_after=cursor, limit=page_size, offset=offset)
                batch = resp.get("expenses", []) if isinstance(resp, dict) else []
                for raw in batch:
                    outcome = mirror_mod.upsert_expense(conn, raw)   # upsert ONCE per expense
                    counts[outcome] = counts.get(outcome, 0) + 1
                if len(batch) < page_size:
                    break
                offset += page_size

            # advance cursor only after a clean pass
            mirror_mod.set_cursor(conn, sync_start)
            s = mirror_mod.stats(conn)
            conn.close()
            logger.info(f"Sync complete ({'full' if full else 'delta'}): {s}")
            return {"mode": "full" if full else "delta", "pages_fetched": pages,
                    "changes": counts, "cursor_before": cursor, "cursor_after": sync_start,
                    "db_stats": s}
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error during sync: {e}")
            raise

    @mcp.tool()
    async def search_expenses(
        query: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        category: Optional[str] = None,
        dated_after: Optional[str] = None,
        dated_before: Optional[str] = None,
        include_deleted: bool = False,
        include_payments: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Search the local mirror (run sync_all first). Full-text over description/details/
        category, plus filters: amount range, user_id, group_id, category, date range.
        Returns matching expenses. Instant, offline, no API calls, no Pro paywall.
        """
        try:
            conn = mirror_mod.connect()
            results = mirror_mod.search(
                conn, query=query, min_amount=min_amount, max_amount=max_amount,
                user_id=user_id, group_id=group_id, category=category,
                dated_after=dated_after, dated_before=dated_before,
                include_deleted=include_deleted, include_payments=include_payments, limit=limit)
            stats = mirror_mod.stats(conn)
            conn.close()
            if not stats["last_synced_at"]:
                return {"warning": "Mirror is empty — run sync_all first.", "results": [], "count": 0}
            return {"count": len(results), "results": results, "last_synced_at": stats["last_synced_at"]}
        except Exception as e:
            logger.error(f"Error searching expenses: {e}")
            raise


# ============================================================================
# Statement Import Tools (bulk import from a parsed statement)
# ============================================================================

def register_statement_tools(mcp: FastMCP) -> None:
    """Register statement-import tools.

    The calling agent reads the raw statement (PDF/CSV/email/screenshot) and passes
    clean rows to import_statement, which returns a reviewable proposal (category +
    split + duplicate flags). After the user approves, confirm_import bulk-creates the
    included rows via the itemization engine. Two-step so nothing is created without review.
    """

    @mcp.tool()
    async def import_statement(
        transactions: List[Dict[str, Any]],
        default_split_name: Optional[str] = None,
        dedup: bool = True,
    ) -> Dict[str, Any]:
        """Turn parsed statement rows into a reviewable import proposal (creates NOTHING).

        transactions: list of {date: 'YYYY-MM-DD', merchant (or description), amount,
                      category? (override), split_ref? (a saved default-split name)}.
        default_split_name: template applied to rows without their own split_ref; if omitted,
                            rows default to 100%-personal (you pay + owe fully).
        dedup: if True, flags rows that match an existing expense in the local mirror
               (same day + same amount) and marks them to skip, so re-importing a statement
               doesn't double-add. (Run sync_all first for dedup to see your existing data.)

        Returns proposals + summary. Review, then call confirm_import with the rows to create.
        """
        try:
            if not transactions:
                raise ValidationError("transactions is required", field="transactions")
            me = (await client.get_current_user())["user"]["id"]
            templates = splits_store.load_all()
            existing = []
            if dedup:
                try:
                    conn = mirror_mod.connect()
                    existing = mirror_mod.search(conn, include_payments=False, limit=10000)
                    conn.close()
                except Exception as e:
                    logger.warning(f"dedup skipped (mirror unavailable): {e}")
            proposal = statement_import_mod.build_proposal(
                transactions, current_user_id=me,
                default_split_name=default_split_name, default_splits=templates,
                existing=existing)
            proposal["note"] = ("Review the proposals. Duplicates are excluded by default. "
                                "Call confirm_import with the rows you want to create.")
            return proposal
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error importing statement: {e}")
            raise

    @mcp.tool()
    async def confirm_import(
        rows: List[Dict[str, Any]],
        group_id: int = 0,
        currency_code: str = "INR",
    ) -> Dict[str, Any]:
        """Bulk-create expenses from approved statement rows (after import_statement review).

        rows: approved items, each {date, description, amount, category?, split_ref?}.
              Rows with a split_ref use that saved template; otherwise 100%-personal.
        group_id: default group for created expenses (0 = non-group). Per-row group_id overrides.
        Each expense is created via the itemization engine (exact paise, reconciled). Returns
        a per-row result list (created id or error). Sequential to stay under rate limits.
        """
        try:
            if not rows:
                raise ValidationError("rows is required", field="rows")
            me = (await client.get_current_user())["user"]["id"]
            templates = splits_store.load_all()
            results = []
            for r in rows:
                desc = r.get("description") or r.get("merchant") or "Imported expense"
                amount = str(r.get("amount"))
                split_ref = r.get("split_ref")
                gid = r.get("group_id", group_id)
                # build a single-item itemized expense (personal if no split_ref)
                if split_ref and split_ref in templates:
                    item = {"desc": desc, "amount": amount, "paid_by": me, "split_ref": split_ref}
                else:
                    item = {"desc": desc, "amount": amount, "paid_by": me,
                            "split": {"type": "exact", "exact": {me: amount}}}  # 100% you
                try:
                    agg = itemize_mod.aggregate_items([item], default_splits=templates)
                    if not agg["reconciled"]:
                        results.append({"description": desc, "created": False,
                                        "error": "reconciliation failed", "discrepancy": agg["discrepancy"]})
                        continue
                    expense_data = {
                        "cost": agg["cost"], "description": desc, "currency_code": currency_code,
                        "group_id": gid, "split_equally": False, "users": agg["users"],
                        "date": (r.get("date") or "") + "T12:00:00Z" if r.get("date") else
                                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "details": f"Imported from statement.\n\n— Automated entry by ExpensifyAI "
                                   f"(github.com/udaysrinu/ExpensifyAI). For any discrepancies or "
                                   f"clarifications, please reach out to Uday.",
                    }
                    resp = await client.create_expense(expense_data)
                    created = resp.get("expenses", [])
                    results.append({"description": desc, "amount": agg["cost"],
                                    "created": bool(created),
                                    "expense_id": created[0]["id"] if created else None,
                                    "errors": resp.get("errors")})
                except Exception as e:
                    results.append({"description": desc, "created": False, "error": str(e)})
            created_n = sum(1 for r in results if r.get("created"))
            logger.info(f"Statement import: created {created_n}/{len(rows)}")
            return {"created": created_n, "total": len(rows), "results": results}
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error confirming import: {e}")
            raise
