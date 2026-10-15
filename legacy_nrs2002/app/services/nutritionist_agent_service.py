import logging
from datetime import datetime
from typing import Any, Dict, List

from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama

from app.chat.nutritionist_tools import NutritionistTools
from app.config import settings
from app.manager.memory_manager import MemoryManager

logger = logging.getLogger("NutritionistAgentService")


class NutritionistAgentService:
    """Nutrition advisor agent service based on ReAct."""

    AGENT_TEMPERATURE = 0.1
    AGENT_MAX_ITERATIONS = 4
    AGENT_VERBOSE = True

    REACT_TEMPLATE = """你是专业营养师助手。你可以通过工具完成计算、评估和建议。

你可以使用以下工具：
{tools}

请严格使用如下格式：
Question: 用户问题
Thought: 你对下一步的思考
Action: 要调用的工具名，必须是 [{tool_names}] 之一
Action Input: 工具输入（优先使用 JSON 对象字符串）
Observation: 工具返回结果
...（Thought/Action/Action Input/Observation 可重复多轮）
Thought: 我已经得到最终答案
Final Answer: 给用户的最终回答（中文）

Question: {input}
Thought:{agent_scratchpad}"""

    def __init__(self, llm_model: str = settings.LLM_MODEL, user_id: str | None = None):
        self.user_id = user_id or self._build_default_user_id()
        self.llm = self._create_llm(llm_model=llm_model)
        self.memory_manager = MemoryManager(user_id=self.user_id)
        self.tools = self._create_tools()
        self.agent_executor = self._initialize_agent()

        logger.info("Nutritionist agent initialized, user_id=%s", self.user_id)

    @staticmethod
    def _build_default_user_id() -> str:
        return f"user_{int(datetime.now().timestamp())}"

    def _create_llm(self, llm_model: str) -> ChatOllama:
        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=llm_model,
            temperature=self.AGENT_TEMPERATURE,
        )

    def _create_tools(self) -> List[Any]:
        return NutritionistTools.get_tools()

    def _build_react_prompt(self) -> PromptTemplate:
        return PromptTemplate.from_template(self.REACT_TEMPLATE)

    def _initialize_agent(self) -> AgentExecutor:
        agent = create_react_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=self._build_react_prompt(),
        )
        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=self.AGENT_VERBOSE,
            handle_parsing_errors=True,
            max_iterations=self.AGENT_MAX_ITERATIONS,
            return_intermediate_steps=True,
        )

    def chat(self, user_input: str) -> Dict[str, Any]:
        try:
            result = self.agent_executor.invoke({"input": user_input})
            final_answer = self._extract_final_answer(result)
            self._persist_conversation(user_input=user_input, final_answer=final_answer)
            return self._success_response(final_answer)
        except Exception as exc:
            logger.error("Agent execution failed, user_id=%s, error=%s", self.user_id, exc, exc_info=True)
            return self._error_response(str(exc))

    @staticmethod
    def _extract_final_answer(result: Dict[str, Any]) -> str:
        return result.get("output") or str(result)

    def _persist_conversation(self, user_input: str, final_answer: str) -> None:
        extracted_entities = self.memory_manager.extract_entities_from_conversation(user_input)
        self.memory_manager.save_conversation(
            user_input=user_input,
            agent_response=final_answer,
            entities=extracted_entities,
        )
        if extracted_entities:
            self.memory_manager.update_user_profile(basic_info=extracted_entities)

    @staticmethod
    def _success_response(final_answer: str) -> Dict[str, Any]:
        return {"success": True, "response_text": final_answer}

    @staticmethod
    def _error_response(error: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error": error,
            "response_text": f"抱歉，处理请求时出错：{error}",
        }

    def _save_user_profile(self) -> None:
        try:
            extracted_entities = self.memory_manager.extracted_entities
            if not extracted_entities:
                logger.info("No extracted entities, skip profile update, user_id=%s", self.user_id)
                return
            self.memory_manager.update_user_profile(basic_info=extracted_entities)
            logger.info("User profile updated, user_id=%s", self.user_id)
        except Exception as exc:
            logger.error("Failed to save user profile, user_id=%s, error=%s", self.user_id, exc, exc_info=True)

    def get_user_profile(self) -> Dict[str, Any]:
        profile = self.memory_manager.get_user_profile(self.user_id)
        if not profile:
            return {}

        return {
            "basic_info": profile.basic_info or {},
            "health_conditions": profile.health_conditions or {},
            "diet_preferences": profile.diet_preferences or {},
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }

    def close(self) -> None:
        self.memory_manager.close()
        logger.info("Nutritionist agent closed, user_id=%s", self.user_id)