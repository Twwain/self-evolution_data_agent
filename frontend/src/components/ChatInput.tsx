/* ════════════════════════════════════════════
 *  聊天输入框 — Kimi 风格: 外框 + 内嵌底部工具栏 (send 按钮贴右下)
 *  autoSize minRows=3, maxRows=10; Enter 发送, Shift+Enter 换行.
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Input } from "antd";
import { ArrowUpOutlined } from "@ant-design/icons";
import styles from "@/styles/query.module.css";

interface Props {
  onSend: (text: string) => void;
  loading?: boolean;
  placeholder?: string;
}

const ChatInput: React.FC<Props> = ({ onSend, loading, placeholder }) => {
  const [text, setText] = useState("");

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const canSend = !loading && text.trim().length > 0;

  return (
    <div className={styles.inputArea}>
      <div className={styles.inputBox}>
        <Input.TextArea
          className={styles.inputTextarea}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || "输入统计需求, 如: 查一下上个月的 DAU 趋势"}
          disabled={loading}
          autoSize={{ minRows: 3, maxRows: 10 }}
          variant="borderless"
        />
        <div className={styles.inputToolbar}>
          <button
            type="button"
            className={canSend ? styles.sendButtonActive : styles.sendButton}
            onClick={handleSend}
            disabled={!canSend}
            aria-label="send"
          >
            <ArrowUpOutlined />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatInput;
