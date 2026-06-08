import { FileSearchOutlined, HistoryOutlined, LockOutlined, UnlockOutlined } from "@ant-design/icons";
import { Button, Space, Tooltip } from "antd";

export function FieldRowActions(props: {
  onEvidence: () => void;
  onHistory: () => void;
  onLock: () => void;
  userLocked: boolean;
}) {
  return (
    <Space size="small">
      <Tooltip title="证据">
        <Button type="text" size="small" icon={<FileSearchOutlined />} aria-label="证据" onClick={props.onEvidence} />
      </Tooltip>
      <Tooltip title="历史">
        <Button type="text" size="small" icon={<HistoryOutlined />} aria-label="历史" onClick={props.onHistory} />
      </Tooltip>
      <Tooltip title={props.userLocked ? "解锁" : "锁定"}>
        <Button
          type="text"
          size="small"
          icon={props.userLocked ? <UnlockOutlined /> : <LockOutlined />}
          aria-label={props.userLocked ? "解锁" : "锁定"}
          onClick={props.onLock}
        />
      </Tooltip>
    </Space>
  );
}
