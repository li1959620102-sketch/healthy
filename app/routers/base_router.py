import functools
import logging
from abc import abstractmethod
from typing import Callable

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

logger = logging.getLogger(__name__)
class BaseRouter:
    """路由基类，提供统一的路由配置"""
    def __init__(self):
        logger.info("running BaseRouter")
        self.router = APIRouter()
    @abstractmethod
    def _register_routes(self) -> APIRouter:
        """
        Set up the API routes for the router.
        This method should be implemented by subclasses to define their specific routes.
        """
        pass
    def get_router(self) -> APIRouter:
        """获取APIRouter实例"""
        return self.router
    def base_endpoint(self, func: Callable):
        """
        A decorator to wrap endpoints in a standard pattern:
         - error handling
         - response shaping
        """
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                func_result = await func(*args, **kwargs)
                if isinstance(func_result, tuple) and len(func_result) == 2:
                    results, outer_kwargs = func_result
                else:
                    results, outer_kwargs = func_result, {}

                if isinstance(results, (StreamingResponse, FileResponse, Response)):
                    return results
                return {"results": results, **outer_kwargs}
            except ValueError as e:
                logger.error(
                    f"Error in base endpoint {func.__name__}() - {str(e)}",
                    exc_info=True,
                )
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException as e:
                logger.error(
                    f"Error in base endpoint {func.__name__}() - {str(e)}",
                    exc_info=True,
                )
                raise e
            except Exception as e:
                logger.error(
                    f"Error in base endpoint {func.__name__}() - {str(e)}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": f"An error '{e}' occurred during {func.__name__}",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                ) from e

        wrapper._is_base_endpoint = True  # type: ignore
        return wrapper