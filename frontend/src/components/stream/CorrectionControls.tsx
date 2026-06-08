/* ════════════════════════════════════════════
 *  CorrectionControls — abort / redirect / param_override 反向通道按钮
 * ----------------------------------------------------------------------------
 *  abort  : 直发 onCorrect("abort", "") 无需输入
 *  redirect / param_override : 开 Modal 取 textarea instruction, 非空才能确认
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Button, Modal, Input, Space } from "antd";
import type { CorrectionAction } from "@/api/correction";

interface Props {
  disabled: boolean;
  onCorrect: (action: CorrectionAction, instruction: string) => void;
}

export const CorrectionControls: React.FC<Props> = ({ disabled, onCorrect }) => {
  const [open, setOpen] = useState<CorrectionAction | null>(null);
  const [text, setText] = useState("");

  const close = () => {
    setOpen(null);
    setText("");
  };

  const confirm = () => {
    if (!open || !text.trim()) return;
    onCorrect(open, text.trim());
    close();
  };

  return (
    <Space>
      <Button danger size="small" disabled={disabled} onClick={() => onCorrect("abort", "")}>
        abort
      </Button>
      <Button size="small" disabled={disabled} onClick={() => setOpen("redirect")}>
        redirect
      </Button>
      <Button size="small" disabled={disabled} onClick={() => setOpen("param_override")}>
        param
      </Button>
      <Modal
        open={open !== null}
        title={`${open ?? ""} instruction`}
        onOk={confirm}
        onCancel={close}
        okText="confirm"
        cancelText="cancel"
        okButtonProps={{ disabled: !text.trim() }}
      >
        <Input.TextArea
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="describe..."
        />
      </Modal>
    </Space>
  );
};

export default CorrectionControls;
